import serial
import time

from . import Frame, ACK, NACK, Error, SAMConfiguration

class ChecksumError(IOError):
	pass

class TimeoutError(IOError):
	pass

class PN532(object):
	TIMEOUT_RES = 0.2
	def __init__(self, port, **opts):
		self.port = port
		opts.setdefault('baudrate', 115200)
		self.serial = serial.serial_for_url(self.port, do_not_open=True, **opts)
		self.serial.bytesize = 8
		self.serial.parity = 'N'
		self.serial.stopbits = 1
		self.serial.rtscts = False
		self.serial.dsrdtr = False
		self.serial.xonxoff = False
		self.serial.timeout = self.TIMEOUT_RES # How long to wait before checking timeout

	def __enter__(self):
		self.serial.open()
		self.send(SAMConfiguration(1, 0, None), preamble="\x55\x55\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0") # Set SAM to normal
		try:
			self.get(timeout=1.0)
		except TimeoutError:
			pass
		else:
			self.get()

	def __exit__(self, *p):
		from . import ACK
		self.send(ACK()) # Cancel outstanding commands so we don't leave the PN532 hanging
		self.serial.close()

	@staticmethod
	def _verifychecksum(*args):
		s = 0
		for thing in args:
			if isinstance(thing, bytes):
				s += sum(map(ord, thing))
			else:
				s += thing
		print "SUM", s
		if (s & 0xFF) != 0:
			raise ChecksumError()
        
	@staticmethod
	def _debug(msg, data):
		txt = ' '.join("%02X" % d for d in map(ord, data))
		if '%' in msg:
			print msg % txt
		else:
			print msg, txt
        
	def send(self, frame, preamble='\0', postamble='\0'):
		"""p.send(Frame)
		Sends the given frame to the connected PN532.
		"""
		msg = preamble+frame.towire()+postamble
		self._debug("SEND", msg)
		self.serial.write(msg)

	def raw_get(self, timeout=None):
		"""p.raw_get() -> Frame
		Reads a frame from the port. Doesn't handle parsing errors (sending
		NACKs).

		FIXME: Timeouts
		"""
		stop = None
		if timeout is not None:
			stop = time.time() + timeout
		header = ""
		while "\x00\xFF" not in header:
			header += self.serial.read(1)
			if stop is not None and stop < time.time():
				raise TimeoutError
		# We have the start of a frame, get to work.
		start = header.index("\x00\xFF")
		header = header[start:]
		while len(header) < 4:
			header += self.serial.read(1)
			if stop is not None and stop < time.time():
				raise TimeoutError
		self._debug("HEADER", header)
		# Now we have enough to begin parsing it out
		LEN = ord(header[2])
		LCS = ord(header[3])
		if LEN == 0 and LCS == 0xFF:
			return ACK()
		elif LEN == 0xFF and LCS == 0:
			return NACK()
		elif LEN == 0xFF and LCS == 0xFF:
			# Extended length
			while len(header) < 7:
				header += self.serial.read(1)
				if stop is not None and stop < time.time():
					raise TimeoutError
			LENM, LENL = map(ord, header[4:6])
			LEN = LENM * 0xFF + LENL
			LCS = ord(header[6])
			self._verifychecksum(LENM, LENL, LCS)
		else:
			# Normal Frame
			self._verifychecksum(LEN, LCS)
		data = self.serial.read(LEN)
		DCS = self.serial.read(1)
		self._debug("DATA/DCS", data+DCS)
		self._verifychecksum(data, DCS)
		TFI = data[0]
		if TFI in "\xD4\xD5":
			ccode = ord(data[1])
			payload = data[2:]
			f = Frame.get_class(ccode).fromwire(payload)
			f.sent = TFI == "\xD4"
			return f
		else:
			# Error Frame
			raise Error(TFI)

	def get(self, timeout=None):
		"""p.get() -> Frame
		Reads a frame from the port. DOES handle parsing errors (sending
		NACKs).
		"""
		while True:
			try:
				rv = self.raw_get(timeout)
				print "RECV", rv
				return rv
			except ChecksumError:
			        print "Checksum Error"
				self.send(NACK())
				
	def doit(self, frame, timeout=None, **kw):
		"""p.doit(Frame) -> Frame
		Execute a command and return the response.

		TODO: Handle timeouts
		"""
		self.send(frame, **kw)
		ack = self.get(timeout=timeout) #TODO: 15ms timeout
		if isinstance(ack, ACK):
		        print "ACK"
			pass # Continue
		else:
			raise IOError("PN532 sent %s when ACK was expected", ack)
		# Not speced to send NACK

		res = self.get(timeout=timeout)
		res.command = frame
		self.send(ACK())
		return res
