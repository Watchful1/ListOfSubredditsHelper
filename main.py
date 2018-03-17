#!/usr/bin/python3

import praw
import os
import logging.handlers
import sys
import configparser
import signal
import time
import traceback
import sqlite3
import re
from datetime import datetime
from datetime import timedelta

### Config ###
LOG_FOLDER_NAME = "logs"
SUBREDDIT = "ListOfSubreddits"
USER_AGENT = "ListOfSubreddits helper (by /u/Watchful1)"
LOOP_TIME = 60 * 60
DATABASE_NAME = "database.db"
LIMIT = 50000

### Logging setup ###
LOG_LEVEL = logging.DEBUG
if not os.path.exists(LOG_FOLDER_NAME):
	os.makedirs(LOG_FOLDER_NAME)
LOG_FILENAME = LOG_FOLDER_NAME+"/"+"bot.log"
LOG_FILE_BACKUPCOUNT = 5
LOG_FILE_MAXSIZE = 1024 * 256 * 16

log = logging.getLogger("bot")
log.setLevel(LOG_LEVEL)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s: %(message)s')
log_stderrHandler = logging.StreamHandler()
log_stderrHandler.setFormatter(log_formatter)
log.addHandler(log_stderrHandler)
if LOG_FILENAME is not None:
	log_fileHandler = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=LOG_FILE_MAXSIZE, backupCount=LOG_FILE_BACKUPCOUNT)
	log_formatter_file = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
	log_fileHandler.setFormatter(log_formatter_file)
	log.addHandler(log_fileHandler)


dbConn = sqlite3.connect(DATABASE_NAME)
c = dbConn.cursor()
c.execute('''
	CREATE TABLE IF NOT EXISTS subreddits (
		ID INTEGER PRIMARY KEY AUTOINCREMENT,
		Subreddit VARCHAR(80) NOT NULL,
		CheckedDate TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
		Subscribers INTEGER DEFAULT 0,
		UNIQUE (Subreddit)
	)
''')
dbConn.commit()


def addSubreddit(subreddit, subscribers):
	c = dbConn.cursor()
	try:
		c.execute('''
			INSERT INTO subreddits
			(Subreddit, Subscribers)
			VALUES (?, ?)
		''', (subreddit, subscribers))
	except sqlite3.IntegrityError:
		return False

	dbConn.commit()
	return True


def updateSubreddit(subreddit, subscribers):
	c = dbConn.cursor()
	c.execute('''
		UPDATE subreddits
		SET Subscribers = ?
			,CheckedDate = CURRENT_TIMESTAMP
		WHERE Subreddit = ?
	''', (subscribers, subreddit))
	dbConn.commit()


def getAllSubreddits():
	c = dbConn.cursor()
	result = c.execute('''
		SELECT Subreddit, CheckedDate, Subscribers
		FROM subreddits
	''')

	out = []
	for subreddit in result.fetchall():
		out.append({'subreddit': subreddit[0],
		            'checkedDate': datetime.strptime(subreddit[1], "%Y-%m-%d %H:%M:%S"),
		            'subscribers': subreddit[2]})

	return out


def signal_handler(signal, frame):
	log.info("Handling interupt")
	sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


log.debug("Connecting to reddit")

once = False
debug = False
user = None
if len(sys.argv) >= 2:
	user = sys.argv[1]
	for arg in sys.argv:
		if arg == 'once':
			once = True
		elif arg == 'debug':
			debug = True
else:
	log.error("No user specified, aborting")
	sys.exit(0)


try:
	r = praw.Reddit(
		user
		,user_agent=USER_AGENT)
except configparser.NoSectionError:
	log.error("User "+user+" not in praw.ini, aborting")
	sys.exit(0)

log.info("Logged into reddit as /u/{}".format(str(r.user.me())))


def getSubredditSubscribers(subredditName):
	subreddit = r.subreddit(subredditName)
	try:
		subscribers = subreddit.subscribers
		return subscribers
	except Exception as err:
		return -1


def addSubToSets(subreddit, subscribers, all, larger, smaller):
	all[subreddit] = subscribers
	if subscribers >= LIMIT:
		larger.add(subreddit)
	else:
		smaller.add(subreddit)


while True:
	startTime = time.perf_counter()
	log.debug("Starting run")

	allSubs = {}
	largerSubs = set()
	smallerSubs = set()
	for subreddit in getAllSubreddits():
		if (((LIMIT * 0.95) < subreddit['subscribers'] < (LIMIT * 1.05)) and (datetime.utcnow() > subreddit['checkedDate'] + timedelta(hours=4))) \
				or (datetime.utcnow() > subreddit['checkedDate'] + timedelta(hours=24)):
			actualSubscribers = getSubredditSubscribers(subreddit['subreddit'])
			log.debug("/r/{} from {} to {}".format(subreddit['subreddit'], subreddit['subscribers'], actualSubscribers))
			updateSubreddit(subreddit['subreddit'], actualSubscribers)
			subreddit['subscribers'] = actualSubscribers

		addSubToSets(subreddit['subreddit'], subreddit['subscribers'], allSubs, largerSubs, smallerSubs)

	for submission in r.subreddit('all').hot(limit=1000):
		subredditName = submission.subreddit.display_name.lower()
		if subredditName not in allSubs:
			subscribers = getSubredditSubscribers(subredditName)
			log.debug("Adding /r/{} with {}".format(subredditName, subscribers))
			addSubreddit(subredditName, subscribers)
			addSubToSets(subredditName, subscribers, allSubs, largerSubs, smallerSubs)

	listWiki = r.subreddit(SUBREDDIT).wiki['listofsubreddits']
	subsInList = re.findall('(?:/r/)([\w-]+)', listWiki.content_md)
	removeSubs = set()
	listSubs = set()
	for sub in subsInList:
		subredditName = sub.lower()
		listSubs.add(subredditName)
		if subredditName not in allSubs:
			subscribers = getSubredditSubscribers(subredditName)
			log.debug("Adding /r/{} with {}".format(subredditName, subscribers))
			addSubreddit(subredditName, subscribers)
			addSubToSets(subredditName, subscribers, allSubs, largerSubs, smallerSubs)

		if subredditName in smallerSubs:
			removeSubs.add(subredditName)

	addSubs = set()
	for sub in largerSubs:
		if sub not in listSubs:
			addSubs.add(sub)

	bldr = []
	bldr.append("Updated: ")
	bldr.append(datetime.utcnow().strftime("%m/%d/%y %I:%M %p UTC"))
	bldr.append("\n\n")

	bldr.append("Add subreddits: ")
	bldr.append(str(len(addSubs)))
	bldr.append("  \n\n")
	for sub in sorted(addSubs, key=allSubs.get)[::-1]:
		bldr.append("* /r/")
		bldr.append(sub)
		bldr.append(" : ")
		bldr.append(str(allSubs[sub]))
		bldr.append("\n")

	bldr.append("\n")
	bldr.append("Remove subreddits: ")
	bldr.append(str(len(removeSubs)))
	bldr.append("  \n\n")
	for sub in sorted(removeSubs, key=allSubs.get)[::-1]:
		bldr.append("* /r/")
		bldr.append(sub)
		bldr.append(" : ")
		bldr.append(str(allSubs[sub]))
		bldr.append("\n")

	log.debug("{} over / {} under | {} add / {} remove".format(len(largerSubs), len(smallerSubs), len(addSubs), len(removeSubs)))

	if debug:
		log.debug(''.join(bldr))
	else:
		addRemoveWiki = r.subreddit(SUBREDDIT).wiki['addremovesubreddits']
		addRemoveWiki.edit(''.join(bldr))

	log.debug("Run complete after: %d", int(time.perf_counter() - startTime))
	if once:
		break
	time.sleep(LOOP_TIME)
