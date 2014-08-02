#!/usr/bin/env python2.7

import subprocess
import MySQLdb
import sqlite3
import imaplib
import smtplib
import email.utils
import re
import uuid
import datetime
import string
import random
import os
import sys

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

def sqlite_bootstrap():
	""" Creates sqlite database, conference_rooms table and pre-populates with conference room details """
	# Connect to local sqlite database
	try:
		conn, cur = sqlite_connect(config['databasefile'])
		cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conference_rooms'")
	
		# Table does not exist
		if not cur.fetchone():
			try:
				print "SQLite database not found, creating",
				# Create it
				cur.execute("""
					CREATE TABLE conference_rooms (
						conf_id INTEGER PRIMARY KEY,
						exten VARCHAR UNIQUE,
						pin VARCHAR,
						last_updated DATE,
						expires_on DATE,
						book_name VARCHAR,
						book_email VARCHAR,
						available INTEGER
					)
				""")
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
			except:
				print bcolors.FAIL + "FAILED" + bcolors.ENDC
				sys.exit()
				
			try:
				print "Pre-populating conference rooms",
			
				# Pre-populate conference rooms
				for i in range(config['start_exten'], config['end_exten']+1):
					cur.execute("INSERT INTO conference_rooms (exten, available) VALUES (?,1)", (i,))
				conn.commit()
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
			except:
				print bcolors.FAIL + "FAILED" + bcolors.ENDC
				sys.exit()
				
		else:
			print "SQLite database found [" + bcolors.OKBLUE + config['databasefile'] + bcolors.ENDC + "]"
	except:
		print bcolors.FAIL + "Fatal error bootstrapping SQLite database" + bcolors.ENDC
		sys.exit()
	finally:
		try:
			cur.close()
		except:
			pass
				
def mysql_connect(host, user, passwd, db):
	""" Connects to MySQL database specified in conferences.conf and returns a cursor """
	try:
		conn = MySQLdb.connect(host=host,user=user,passwd=passwd,db=db)
		return conn, conn.cursor()
	except:
		print bcolors.FAIL + "Failed to connect to MySQL database [" + host + ", " + user + "]" + bcolors.ENDC
		sys.exit()
	
def sqlite_connect(databasefile):
	""" Connects to sqlite database file specified in conferences.conf and returns a cursor """
	try:
		databasefile = os.path.join(dir, databasefile)
		conn = sqlite3.connect(databasefile)
		return conn, conn.cursor()
	except:
		print bcolors.FAIL + "Failed to connect to SQLite database [" + databasefile + "]" + bcolors.ENDC
		sys.exit()

def get_new_emails():
	""" Fetch new emails from specified IMAP server and account. Returns a list of all new emails. """
	try:
		# Connect to IMAP account
		print "Connecting to IMAP account [" + bcolors.OKBLUE + config['imap_server'] + ", " + config['imap_username'] + bcolors.ENDC + "]",
		imap_con = imaplib.IMAP4_SSL(config['imap_server'])
		imap_con.login(config['imap_username'], config['imap_password'])
		imap_con.select()
		print bcolors.OKGREEN + "OK" + bcolors.ENDC

		try:
			# Retrieve all unread emails
			print "Retrieving unread emails",
			rcode, data = imap_con.search(None, '(UNSEEN)')

			if rcode == 'OK':
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
				try:
					# Process emails
					print "Processing emails",
					emails = []
					for num in data[0].split():
						rcode, data = imap_con.fetch(num, '(RFC822)')
						if rcode == 'OK':
							emails.append(data)
					print bcolors.OKGREEN + "OK" + bcolors.ENDC
					return emails
				except:
					print bcolors.WARNING + "Unable to process emails" + bcolors.ENDC
			else:
				print bcolors.WARNING + "Unable to fetch emails" + bcolors.ENDC
		except:
			print bcolors.WARNING + "Unable to retrieve emails" + bcolors.ENDC
	except:
		print bcolors.FAIL + "FAILED" + bcolors.ENDC
		sys.exit()
	finally:
		# ALWAYS do this regardless of what happens above
		try:
			imap_con.close()
			imap_con.logout()
		except:
			pass

def process_emails(emails):
	""" Loops through a list of email (most likely from get_new_emails()) and returns a list of senders. """
	if emails:
		requests = []
		
		# For every email returned
		for data in emails:
			sender = {}
			
			# Let's find the sender's details in the email
			for email_part in data:
				if isinstance(email_part, tuple):
					msg = email.message_from_string(email_part[1])
					sender_string = msg['from']
					sender_parts = email.utils.parseaddr(sender_string)
					sname, semail = '', ''
					
					# Find an email address
					for s in sender_parts:
						if re.match(r"[^@]+@[^@]+\.[^@]+", s):
							semail = s
						else:
							sname = s

					if len(semail) > 3: # An email address was found!
						sender['email'] = semail
						
						if sname == '':
							sname = semail.split('@', 1)[0]
						sender['name'] = sname
						sender['created'] = 0
						sender['conf'] = ''
						requests.append(sender)
		return requests
	else:
		print bcolors.WARNING + "Unable to process emails" + bcolors.ENDC
		return None
		
def pin_generator():
	""" Generates a pin code for conferences based on pin_length set in the configuration file """
	return ''.join(random.choice(string.digits) for x in range(config['pin_length']))
	
def create_conferences(requests):
	""" Creates and updates conferences for each request """
	# Connect to MySQL / Asterisk database
	mconn, mcur = mysql_connect(config['hostname'], config['username'], config['password'], config['database'])
	
	# Connect to local sqlite database
	sconn, scur = sqlite_connect(config['databasefile'])
	
	# For every request
	for index, request in enumerate(requests):
	
		print "Processing request for " + bcolors.OKBLUE + request['email'] + bcolors.ENDC
	
		# First - Find a free conference room
		scur.execute("SELECT conf_id, exten FROM conference_rooms WHERE available=1 ORDER BY exten LIMIT 1")
		free_conf = scur.fetchone()
		
		# Do we have a free/valid conference?
		if free_conf:
			try:
				conference_id = free_conf[0]
				conference_exten = free_conf[1]
				conference_pin = pin_generator()
				
				# Reserve the room
				print "Reserving conference room " + bcolors.OKBLUE + conference_exten + bcolors.ENDC,
				scur.execute("UPDATE conference_rooms SET available=0, last_updated=?, expires_on=?, book_name=?, book_email=?, pin=? WHERE conf_id=?", (datetime.datetime.now(), datetime.datetime.now()+ datetime.timedelta(days=config['conf_expire']), str(request['name']), str(request['email']), str(conference_pin), str(conference_id)))
								
				# Does the conference room exist in FreePBX?
				mcur.execute("SELECT exten FROM meetme WHERE exten=%s AND exten BETWEEN CAST(%s as UNSIGNED) AND CAST(%s as UNSIGNED) LIMIT 1", (str(conference_exten), str(config['start_exten']), str(config['end_exten'])))
				conf_exists = mcur.fetchone()

				if conf_exists: # Conf exists - Update it
					mcur.execute("UPDATE meetme SET userpin=%s, users=0 WHERE exten=%s AND description=CONCAT('Room ', %s) LIMIT 1",(str(conference_pin), str(conference_exten), str(conference_id)))
					
				else: # Conf does not exist - Create it
					mcur.execute("INSERT INTO meetme (exten, options, userpin, adminpin, description, joinmsg_id, music, users) VALUES (%s, %s, %s, '', %s, 0, 'default', 0)", (str(conference_exten), str(config['conf_options']), str(conference_pin), 'Room ' + str(conference_id)))

				requests[index]['created'] = 1
				requests[index]['conf'] = conference_exten
				requests[index]['pin'] = conference_pin
				requests[index]['expires'] = (datetime.datetime.now() + datetime.timedelta(days=int(config['conf_expire']))).strftime("%a %e %b %T")
				
				# Commit changes to the databases & close connections
				sconn.commit()
				scur.close()
				mconn.commit()
				mcur.close()
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
			except:
				print bcolors.FAIL + "FAILED" + bcolors.ENDC
		else:
			print bcolors.WARNING + "No free conference rooms!" + bcolors.ENDC
	return requests
	
def send_details(requests):
	""" Sends details of the conference room to the requester """
	try:
		print "Connecting to SMTP server [" + bcolors.OKBLUE + config['smtp_server'] + ", " + config['smtp_sender'] + bcolors.ENDC + "]",
		sender = config['smtp_sender']
		s = smtplib.SMTP(config['smtp_server'])
		
		if config['smtp_auth']:
			s.login(config['smtp_username'], config['smtp_password'])
			
		print bcolors.OKGREEN + "OK" + bcolors.ENDC
		
		
		for request in requests:
			try:
				print "Sending conference details to " + bcolors.OKBLUE + request['email'] + bcolors.ENDC + "]",
				if request['created'] == 1:
					msg = "From:" + sender + "\nTo:" + request['email'] + "\nSubject: " + config['smtp_subject'] + " \n\nConference Number: " + request['conf'] + "\nConference Pin: " + request['pin'] + "\nExpires: " + request['expires'] + "\n\n" + config['smtp_message']
					s.sendmail(config['smtp_sender'], request['email'], msg)
				else:
					msg = "From:" + sender + "\nTo:" + request['email'] + "\nSubject: " + config['smtp_subject_norooms'] + " \n\n" + config['smtp_message_norooms']
					s.sendmail(config['smtp_sender'], request['email'], msg)
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
			except: 
				print bcolors.FAIL + "FAILED" + bcolors.ENDC
		s.quit()
		
	except:
		print bcolors.FAIL + "FAILED" + bcolors.ENDC

def apply_config():
	""" Builds FreePBX/Asterisk config from DB and reloads config """
	try:
		print "Apply configurations",
		# Rebuild confs
		subprocess.call(config['retrieve_conf_bin'], shell=True, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT)
		# Reload confs
		subprocess.call(config['amportal_bin'] + ' a r', shell=True, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT)
		print bcolors.OKGREEN + "OK" + bcolors.ENDC
		return True
	except:
		print bcolors.FAIL + "FAILED" + bcolors.ENDC
		sys.exit()

def cleanup_conferences():
	""" Cleans up any expired conferences and sets a random pin """
	# Connect to MySQL / Asterisk database
	mconn, mcur = mysql_connect(config['hostname'], config['username'], config['password'], config['database'])
	
	# Connect to local sqlite database
	sconn, scur = sqlite_connect(config['databasefile'])
	
	# Find conference rooms that have expired
	scur.execute("SELECT conf_id, exten, expires_on FROM conference_rooms WHERE available=0")
	booked_confs = scur.fetchall()
	
	expired_confs = []
	
	# This is a bit hacky :S (replace later)
	if booked_confs:
		for conference in booked_confs:
			expiry_date = conference[-1].split('.')[:-1][0]
			
			if datetime.datetime.strptime(expiry_date, '%Y-%m-%d %H:%M:%S') < datetime.datetime.now():
				expired_confs.append(conference)
	
	if expired_confs:
		for conference in expired_confs:
			try:
				conference_id = conference[0]
				conference_exten = conference[1]
				conference_pin = pin_generator()
				
				print "Expiring conference " + bcolors.OKBLUE + conference_exten + bcolors.ENDC,

				# Change the pin on all expired conferences
				mcur.execute("UPDATE meetme SET userpin=%s, users=0 WHERE exten=%s AND description=CONCAT('Room ', %s) LIMIT 1",(str(conference_pin), str(conference_exten), str(conference_id)))

				# Switch the conference room flag to available
				scur.execute("UPDATE conference_rooms SET available=1, book_name='', book_email='', pin=? WHERE conf_id=?", (str(conference_id), str(conference_pin)))
				
				print bcolors.OKGREEN + "OK" + bcolors.ENDC
			except:
				print bcolors.FAIL + "FAILED" + bcolors.ENDC
				sys.exit()
				
	else:
		print "No expired conferences found"
			
	mconn.commit()
	mcur.close()
	sconn.commit()
	scur.close()
			
def main():
	print bcolors.HEADER + "Starting..." + bcolors.ENDC
	# Create and populate sqlite database if it does not exist
	sqlite_bootstrap()

	# Get all new unread emails
	emails = get_new_emails()

	# We found some unread emails! :)
	if emails:
		requests = process_emails(emails)

		# We found some requests!! :D
		if requests:
			print bcolors.OKBLUE + str(len(emails)) + bcolors.ENDC + " request(s) found"
			
			print bcolors.HEADER + "Setting up conference rooms" + bcolors.ENDC
			
			requests = create_conferences(requests)
			if apply_config():
				send_details(requests)
	
		else:
			print bcolors.WARNING + "0" + bcolors.ENDC + " request(s) found"
				
	# Now the requests are processed, let's do some cleaning up
	print bcolors.HEADER + "Cleanup expired conferences" + bcolors.ENDC
	cleanup_conferences()
	
	print bcolors.HEADER + "Finished!" + bcolors.ENDC
	
if __name__ == "__main__":
	config = {}
	dir = os.path.dirname(__file__)
	config_file = os.path.join(dir, 'conferences.conf')
	execfile(config_file, config)
	main()