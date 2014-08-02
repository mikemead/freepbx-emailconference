#!/usr/bin/env python2.7

from subprocess import call
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

def sqlite_bootstrap():
	""" Creates sqlite database, conference_rooms table and pre-populates with conference room details """
	# Connect to local sqlite database
	try:
		conn, cur = sqlite_connect(config['databasefile'])
		cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conference_rooms'")
	
		# Table does not exist
		if not cur.fetchone():
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
		
			# Pre-populate conference rooms
			for i in range(config['start_exten'], config['end_exten']+1):
				cur.execute("INSERT INTO conference_rooms (exten, available) VALUES (?,1)", (i,))
			conn.commit()
	except:
		print "Error - Problem bootstrapping sqlite database"
	finally:
		try:
			cur.close()
		except:
			pass
				
def mysql_connect(host, user, passwd, db):
	""" Connects to MySQL database specified in conferences.conf and returns a cursor """
	conn = MySQLdb.connect(host=host,user=user,passwd=passwd,db=db)
	return conn, conn.cursor()
	
def sqlite_connect(databasefile):
	""" Connects to sqlite database file specified in conferences.conf and returns a cursor """
	databasefile = os.path.join(dir, databasefile)
	conn = sqlite3.connect(databasefile)
	return conn, conn.cursor()

def get_new_emails():
	""" Fetch new emails from specified IMAP server and account. Returns a list of all new emails. """
	try:
		# Connect to IMAP account
		imap_con = imaplib.IMAP4_SSL(config['imap_server'])
		imap_con.login(config['imap_username'], config['imap_password'])
		imap_con.select()

		try:
			# Retrieve all unread emails
			rcode, data = imap_con.search(None, '(UNSEEN)')

			if rcode == 'OK':
				try:
					# Process emails
					emails = []
					for num in data[0].split():
						rcode, data = imap_con.fetch(num, '(RFC822)')
						if rcode == 'OK':
							emails.append(data)
					return emails
				except:
					print "Error - Processing emails"
			else:
				print "Error - Fetching emails"
		except:
			print "Error - Unable to retrieve emails"
	except:
		print "Error - Connecting to IMAP mailbox"
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
		print "Error - Processing emails"
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
			except:
				print "Error - Could not reserve conference room"
		else:
			print "Error - No free/valid conference room"
	return requests
	
def send_details(requests):
	""" Sends details of the conference room to the requester """
	sender = config['smtp_sender']
	s = smtplib.SMTP(config['smtp_server'])
	if config['smtp_auth']:
		s.login(config['smtp_username'], config['smtp_password'])
	
	for request in requests:
		if request['created'] == 1:
			msg = "From:" + sender + "\nTo:" + request['email'] + "\nSubject: " + config['smtp_subject'] + " \n\nConference Number: " + request['conf'] + "\nConference Pin: " + request['pin'] + "\nExpires: " + request['expires'] + "\n\n" + config['smtp_message']
        	        s.sendmail(config['smtp_sender'], request['email'], msg)
		else:
			msg = "From:" + sender + "\nTo:" + request['email'] + "\nSubject: " + config['smtp_subject_norooms'] + " \n\n" + config['smtp_message_norooms']
        	        s.sendmail(config['smtp_sender'], request['email'], msg)
	s.quit()

def apply_config():
	""" Builds FreePBX/Asterisk config from DB and reloads config """
	try:
		# Rebuild confs
		call(config['retrieve_conf_bin'], shell=True)
		# Reload confs
		call(config['amportal_bin'] + ' a r', shell=True)
		return True
	except:
		return None

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
				
				# Change the pin on all expired conferences
				mcur.execute("UPDATE meetme SET userpin=%s, users=0 WHERE exten=%s AND description=CONCAT('Room ', %s) LIMIT 1",(str(conference_pin), str(conference_exten), str(conference_id)))

				# Switch the conference room flag to available
				scur.execute("UPDATE conference_rooms SET available=1, book_name='', book_email='', pin=? WHERE conf_id=?", (str(conference_id), str(conference_pin)))
				
			except:
				print "Error - Cleaning up conferences"
			
	mconn.commit()
	mcur.close()
	sconn.commit()
	scur.close()
			
def main():
	# Create and populate sqlite database if it does not exist
	sqlite_bootstrap()

	# Get all new unread emails
	emails = get_new_emails()

	# We found some unread emails! :)
	if emails:
		requests = process_emails(emails)

		# We found some requests!! :D
		if requests:
			requests = create_conferences(requests)
			if apply_config():
				send_details(requests)

	# Now the requests are processed, let's do some cleaning up
	cleanup_conferences()
	
if __name__ == "__main__":
	config = {}
	dir = os.path.dirname(__file__)
	config_file = os.path.join(dir, 'conferences.conf')
	execfile(config_file, config)
	main()