import boto3
import botocore.exceptions
import csv
import datetime
import pathlib
import sys
import yaml

#vars
ConfigData = {
	'blockDeviceData' : [],
	'imageType' : None,
	'instances' : None,
	'instanceType' : None,
	'architecture' : None,
	'rootType' : None,
	'minCount' : None,
	'maxCount' : None,
	'keyFile' : None,
	'launchData' : '''#!/bin/bash -ex
		exec > >(tee /var/log/user-data.log|logger -t user-data) 2>&1
		yum update -y''',
	'users' : [],
	'volumes' : []
}
LOG_NAME = 'automation_launch_log'

#VM Config File
def loadConfigs(filePath, configData):
	try:
		inStream = open(filePath)
		configs = yaml.load(inStream, Loader=yaml.BaseLoader)
		configData['instanceType'] = configs['server']['instance_type']
		configData['imageType'] = configs['server']['ami_type']
		configData['keyFile'] = configs['server']['key_file']
		configData['architecture'] = configs['server']['architecture']
		configData['rootType'] = configs['server']['root_device_type']
		configData['minCount'] = configs['server']['min_count']
		configData['maxCount'] = configs['server']['max_count']
		for user in configs['server']['users']:
			configData['users'].append({
				'Login' : user['login'],
				'PublicKey' : user['ssh_key']
			})
		configData['volumes'] = configs['server']['volumes']
		return True
	except:
		return False

#Check for permission key file, create if necessary
def checkKeyFile(configData):
	keyPairFile = pathlib.Path(configData['keyFile'])
	if not keyPairFile.exists():
		client.delete_key_pair(KeyName=configData['keyFile'][:-4])
		keyPair = client.create_key_pair(KeyName=configData['keyFile'][:-4])
		keyPairData = str(keyPair['KeyMaterial'])
		keyPairFile = open(configData['keyFile'],'w')
		keyPairFile.write(keyPairData)
		return False
	return True

#Format and add config data to the launch data command for the volumes and users
def prepareData(configData):
	#Volume creation
	for volume in configData['volumes']:
		configData['blockDeviceData'].append({
			'DeviceName' : volume['device'],
			'Ebs' : {
				'VolumeSize' : int(volume['size_gb'])
			}
		})
		if volume['mount'] != '/':
			configData['launchData'] = configData['launchData'] + '\nmkfs -t ' + volume['type'] + ' ' + volume['device']
			configData['launchData'] = configData['launchData'] + '\nmkdir ' + volume['mount']
			configData['launchData'] = configData['launchData'] + '\nmount ' + volume['device'] + ' ' + volume['mount'] + '/'
	#User creation
	for user in configData['users']:
		if len(configData['users']) > 1:
			configData['launchData'] = configData['launchData'] + '\nsu -'
		configData['launchData'] = configData['launchData'] + '\nadduser ' + user['Login']
		configData['launchData'] = configData['launchData'] + '\nsu - ' + user['Login']
		configData['launchData'] = configData['launchData'] + '\ncd /home/' + user['Login']
		configData['launchData'] = configData['launchData'] + '\nmkdir .ssh'
		configData['launchData'] = configData['launchData'] + '\nchmod 700 .ssh'
		configData['launchData'] = configData['launchData'] + '\ntouch .ssh/authorized_keys'
		configData['launchData'] = configData['launchData'] + '\nchmod 600 .ssh/authorized_keys'
		configData['launchData'] = configData['launchData'] + '\necho \'' + user['PublicKey'] + '\' >> .ssh/authorized_keys'
		configData['launchData'] = configData['launchData'] + '\nchown ' + user['Login'] + ':' + user['Login'] + ' .ssh'
		configData['launchData'] = configData['launchData'] + '\nchown ' + user['Login'] + ':' + user['Login'] + ' .ssh/authorized_keys'
	
#Create and save a reference to the virtual instance
def createInstance(configData):
	configData['instances'] = resource.create_instances(
		ImageId = configData['imageType'],
		MinCount = int(configData['minCount']),
		MaxCount = int(configData['maxCount']),
		InstanceType = configData['instanceType'],
		KeyName = configData['keyFile'][:-4],
		BlockDeviceMappings = configData['blockDeviceData'],
		UserData = configData['launchData']
	)

#Wait for instance to be ready
def waitForInstance(delay, attempts, configData):
	print('Waiting for instances...')
	waiter = client.get_waiter('instance_status_ok')
	instanceIds = []
	for instance in configData['instances']:
		instanceIds.append(instance.instance_id)
	waiter.wait(
		InstanceIds = instanceIds, 
		Filters = [{
			'Name' : 'instance-status.status',
			'Values' : ['ok']
		}],
		WaiterConfig = {
			'Delay' : delay,
			'MaxAttempts' : attempts
		}
	)
	
#Log & Print (ASSUMES file IS OPEN)
def log(text, file, logBool, printBool):
	if logBool:
		file.write('[' + datetime.datetime.now().strftime('%m/%d/%Y-%X') + '] ' + text + '\n')
	if printBool:
		print(text)

'''--------'''
'''- MAIN -'''
'''--------'''
#Logging
date = datetime.datetime.now()
logFile = open(LOG_NAME + '_' + date.strftime('%m-%d-%Y') + '.txt','a')
log('Initiating AWS EC2 automation', logFile, True, True)

#EC2 Client & Resource
client = boto3.client('ec2', region_name='us-east-2')
resource = boto3.resource('ec2', region_name='us-east-2')

#Configs
configsLoaded = loadConfigs('config.yaml', ConfigData)
if not configsLoaded:
	log('Error loading configs, closing...', logFile, True, True)
	sys.exit()

#Key check
if checkKeyFile(ConfigData):
	log('Permission key file found', logFile, True, True)
else:
	log('Permission key file not found, creating now...', logFile, True, True)

#dry-run instance creation
try:
	instances = resource.create_instances(
		ImageId = ConfigData['imageType'],
		MinCount = int(ConfigData['minCount']),
		MaxCount = int(ConfigData['maxCount']),
		InstanceType = ConfigData['instanceType'],
		KeyName = ConfigData['keyFile'][:-4],
		DryRun = True
	)
except botocore.exceptions.ClientError as e:
	if 'UnauthorizedOperation' in str(e):
		log('Error: UnauthorizedOperation - Instance creation dry-run failed', logFile, True, True)
		sys.exit()
	elif 'DryRunOperation' in str(e):
		log('Instance creation dry-run success!', logFile, True, True)
	else:
		raise
		sys.exit()

#Create instance
prepareData(ConfigData)
createInstance(ConfigData)
waitForInstance(20, 30, ConfigData) #Wait for at most 10 minutes

	
#Display created instance information
for instance in ConfigData['instances']:
	log('----------------------------------', logFile, True, True)
	log('Instance created successfully!', logFile, True, True)
	log('Instance ID: ' + instance.instance_id, logFile, True, True)
	log('Instance DNS: ' + client.describe_instances(InstanceIds = [ConfigData['instances'][0].instance_id])['Reservations'][0]['Instances'][0]['NetworkInterfaces'][0]['Association']['PublicDnsName'], logFile, True, True)
	