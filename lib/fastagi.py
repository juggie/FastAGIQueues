import asyncore, asynchat, socket, redis, json

class FAGIServer(asyncore.dispatcher):
	def __init__(self, logger, config):
		asyncore.dispatcher.__init__(self)
		#save logger object
		self._logger = logger

		#clients
		self._clients = {}

		#asynccore
		self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
		self.set_reuse_addr()
		self.bind(("", config.fastagi_port))
		self.listen(5)

		#redis
		self._redis_globalchannel = 'agiqueues.global'
		self._redis_instance_channel = 'agiqueues.%s' % config.instancename #get instance name from config, default to hostname
		self._redis = redis.StrictRedis(host='192.168.99.20', port=6379, db=0) #get redis server/port from config		
		logger.Message('AGI Queue Server listening on port: %i' % config.fastagi_port, 'FASTAGI')

	def handle_accept(self):
		pair = self.accept()
		if pair is None:
			pass
		else:
			sock, addr = pair
			handler = FAGIChannel(self, sock, addr, self._logger, self._clients)
	
	def numclients(self):
		return len(self)

	def getclient(self, clientMD5):
		return self._clients[clientMD5]

class FAGIChannel(asynchat.async_chat):
	def __init__(self, agiserver, sock, addr, logger, clients):
		#save logger object
		self._logger = logger
		
		#save clients object
		self._clients = clients
		
		asynchat.async_chat.__init__(self, sock)
		#fagi terminator
		self.set_terminator("\n")
		#buffer for asynchat
		self._buffer = []
		#ip/port in string
		self._straddr = str(addr)
		#did we complete the initial agi connection
		self._connected = False
		#is moh enabled
		self._moh = False
		
		#set the parent class name
		self._agiserver = agiserver
		
		#set md5 id of connected client
		self._clientMD5 = 'test' #hashlib.md5(str(addr)).hexdigest()

		self._clients[self._clientMD5]=self

		logger.Message('Incoming FastAGI connection from %s' % repr(addr), 'FASTAGI')
		#logger.Message('%i Connected FastAGI sessions' % len(CONNECTEDCLIENTS), 'FASTAGI')

	def handle_redis_event(self, event):
		self._logger.Message('Event %s' % event, 'REDIS')
		if event['event']=='answer':
			self.AGI_Answer()
		elif event['event']=='playback':
			self.AGI_Playback() 
		elif event['event']=='mohon':
			self.AGI_MusicOnHold(True) 
		elif event['event']=='mohoff':
			self.AGI_MusicOnHold(False) 
		elif event['event']=='hangup':
			self.AGI_Hangup() 

	def send_redis_event(self, event):
		tosend = json.dumps({'event' : event, 'clientMD5' : self._clientMD5,'instance_channel' : self._agiserver._redis_instance_channel})
		self._agiserver._redis.publish(self._agiserver._redis_globalchannel, tosend)
		self._logger.Message('Sent %s on channel: %s' % (tosend, self._agiserver._redis_globalchannel), 'REDIS')

	def send_command(self, data):
		self._logger.Message('SENT: %s' % data, 'AGI')
		self.push(data+'\n')
		
	def collect_incoming_data(self, data):
		# Append incoming data to the buffer
		self._buffer.append(data)

	def found_terminator(self):
		line = "".join(self._buffer)
		self._buffer = []
		self.handle_line(line)

	def handle_line(self, line):
		if self._connected == False:
			if line == '':
				self._connected = True
				self._logger.Message('Initial Variables Received', 'AGI')
				self.send_redis_event('ring')
			else:
				self._logger.Message('Variable -> %s' % line, 'AGI')
		else:
			self._logger.Message('Received %s' % line, 'AGI')
			self.HandleCall(line)
			
	def handle_close(self):
		self._logger.Message('FastAGI connection from %s closed' % self._straddr, 'AGI')
		if self._clientMD5 in self._clients: del self._clients[self._clientMD5]
		self.close()

	def handle_errorr(self):
		self._logger.Message('ERROR: FastAGI connection from %s closed' % self._straddr, 'AGI')
		if self._clientMD5 in self._clients: del self._clients[self._clientMD5]
		self.close()
	
	#parsing of agi responses goes here
	def HandleCall(self,line):
		if line[:3] == '200':
			self.send_redis_event('ok')
		elif line[:3] == '510':
			self.send_redis_event('invalid')
		elif line[:3] == '511':
			self.send_redis_event('dead')
		elif line == 'HANGUP':
			self.send_redis_event('hangup')
			self.handle_close()
		else:
			self._logger.Message('Unknown event: %s' % line, 'AGI')
			
	def AGI_Answer(self):
		self.send_command('ANSWER')

	def AGI_Playback(self, file='beep'):
		self.send_command('STREAM FILE %s \'\' 0' % file)
		
	def AGI_MusicOnHold(self, moh_state, moh_class = ''):
		if moh_state == True:
			self._moh = True 
			self.send_command('SET MUSIC ON %s' % moh_class)
		else:
			self._moh = False
			self.send_command('SET MUSIC OFF')

	def AGI_Hangup(self):
		self.send_command('HANGUP')
		