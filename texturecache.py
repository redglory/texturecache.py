#!/usr/bin/env python
# -*- coding: utf-8 -*-

################################################################################
#
#  Copyright (C) 2013 Neil MacLeod (texturecache@nmacleod.com)
#
#  This Program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2, or (at your option)
#  any later version.
#
#  This Program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# Simple utility to query, validate, clean and refresh the XBMC texture cache.
#
# https://github.com/MilhouseVH/texturecache.py
#
# Usage:
#
#  See built-in help (run script without parameters), or the README file
#  on github for more details.
#
################################################################################

import sqlite3 as lite
import os, sys, json, re, datetime, time
import socket, base64, hashlib
import threading
import errno, codecs

if sys.version_info >= (3, 0):
  import configparser as ConfigParser
  import io as StringIO
  import http.client as httplib
  import urllib.request as urllib2
  import queue as Queue
else:
  import ConfigParser, StringIO, httplib, urllib2, Queue

#
# Config class. Will be a global object.
#
class MyConfiguration(object):
  def __init__( self, argv ):

    self.VERSION="0.7.3"

    self.GITHUB = "https://raw.github.com/MilhouseVH/texturecache.py/master"

    self.DEBUG = True if "PYTHONDEBUG" in os.environ and os.environ["PYTHONDEBUG"].lower()=="y" else False

    self.HAS_PVR = False

    namedSection = False

    self.GLOBAL_SECTION = "global"
    self.THIS_SECTION = self.GLOBAL_SECTION
    self.CONFIG_NAME = "texturecache.cfg"

    serial_urls = "assets\.fanart\.tv"
    embedded_urls = "image://video, image://music"

    config = ConfigParser.SafeConfigParser()

    #Use @section argument if passed on command line
    #Use list(argv) so that a copy of argv is iterated over, making argv.remove() safe to use.
    for arg in list(argv):
      if arg.startswith("@section") and arg.find("=") != -1:
        self.THIS_SECTION = arg.split("=", 1)[1].strip()
        namedSection = True
        argv.remove(arg)

    #Use @config if passed on command line
    for arg in list(argv):
      if arg.startswith("@config") and arg.find("=") != -1:
        self.CONFIG_NAME = arg.split("=", 1)[1].strip()
        argv.remove(arg)

    # Use the default or user specified config filename.
    # If it doesn't exist and is a relative filename, try and find a config
    # file in the current directory, otherwise look in same directory as script itself.
    self.FILENAME = os.path.expanduser(self.CONFIG_NAME)
    if not os.path.exists(self.FILENAME) and not os.path.isabs(self.FILENAME):
      self.FILENAME = "%s%s%s" % (os.getcwd(), os.sep, self.CONFIG_NAME)
      if not os.path.exists(self.FILENAME):
        self.FILENAME = "%s%s%s" % (os.path.dirname(__file__), os.sep, self.CONFIG_NAME)

    cfg = StringIO.StringIO()
    cfg.write("[%s]\n" % self.GLOBAL_SECTION)

    if os.path.exists(self.FILENAME):
      cfg.write(open(self.FILENAME, "r").read())
      cfg.write("\n")

    cfg.seek(0, os.SEEK_SET)
    config.readfp(cfg)

    # If a specific section is not passed on the command line, check the config
    # to see if there is a default section property, and if not then default to
    # the global default section.
    if not namedSection:
      self.THIS_SECTION = self.getValue(config, "section", self.GLOBAL_SECTION)

    # Add the named section if not already present
    if not config.has_section(self.THIS_SECTION):
      config.add_section(self.THIS_SECTION)

    #Add any command line settings - eg. @xbmc.host=192.168.0.8 - to the named section.
    for arg in list(argv):
      if arg.startswith("@") and arg.find("=") != -1:
        key_value = arg[1:].split("=", 1)
        config.set(self.THIS_SECTION, key_value[0].strip(), key_value[1].strip())
        argv.remove(arg)

    self.IDFORMAT = self.getValue(config, "format", "%06d")
    self.FSEP = self.getValue(config, "sep", "|")

    self.XBMC_BASE = os.path.expanduser(self.getValue(config, "userdata", "~/.xbmc/userdata"))
    self.TEXTUREDB = self.getValue(config, "dbfile", "Database/Textures13.db")
    self.THUMBNAILS = self.getValue(config, "thumbnails", "Thumbnails")

    if self.XBMC_BASE[-1:] != "/": self.XBMC_BASE += "/"
    if self.THUMBNAILS[-1:] != "/": self.THUMBNAILS += "/"

    self.XBMC_BASE = self.XBMC_BASE.replace("/", os.sep)
    self.TEXTUREDB = self.TEXTUREDB.replace("/", os.sep)
    self.THUMBNAILS = self.THUMBNAILS.replace("/", os.sep)

    self.XBMC_HOST = self.getValue(config, "xbmc.host", "localhost")
    self.WEB_PORT = self.getValue(config, "webserver.port", "8080")
    self.RPC_PORT = self.getValue(config, "rpc.port", "9090")
    self.WEB_SINGLESHOT = self.getBoolean(config, "webserver.singleshot", "no")
    web_user = self.getValue(config, "webserver.username", "")
    web_pass = self.getValue(config, "webserver.password", "")

    if (web_user and web_pass):
      token = '%s:%s' % (web_user, web_pass)
      if sys.version_info >= (3, 0):
        self.WEB_AUTH_TOKEN = base64.encodestring(bytes(token, "utf-8")).decode()
      else:
        self.WEB_AUTH_TOKEN = base64.encodestring(token)
      self.WEB_AUTH_TOKEN = self.WEB_AUTH_TOKEN.replace('\n', '')
    else:
      self.WEB_AUTH_TOKEN = None

    self.DOWNLOAD_THREADS_DEFAULT = int(self.getValue(config, "download.threads", "2"))

    self.DOWNLOAD_THREADS = {}
    for x in ["addons", "albums", "artists", "songs", "movies", "sets", "tags", "tvshows", "pvr.tv", "pvr.radio"]:
      temp = int(self.getValue(config, "download.threads.%s" % x, self.DOWNLOAD_THREADS_DEFAULT))
      self.DOWNLOAD_THREADS["download.threads.%s" % x] = temp

    self.SINGLETHREAD_URLS = self.getPatternFromList(config, "singlethread.urls", serial_urls)

    self.XTRAJSON = {}
    self.QA_FIELDS = {}

    self.QA_FIELDS["qa.art.artists"] = "fanart, thumbnail"
    self.QA_FIELDS["qa.art.albums"] = "fanart, thumbnail"
    self.QA_FIELDS["qa.art.songs"] = "fanart, thumbnail"

    self.QA_FIELDS["qa.blank.movies"] = "plot, mpaa"
    self.QA_FIELDS["qa.art.movies"] = "fanart, poster"

    self.QA_FIELDS["qa.art.sets"] = "fanart, poster"

    self.QA_FIELDS["qa.blank.tvshows.tvshow"] = "plot"
    self.QA_FIELDS["qa.blank.tvshows.episode"] = "plot"
    self.QA_FIELDS["qa.art.tvshows.tvshow"] = "fanart, banner, poster"
    self.QA_FIELDS["qa.art.tvshows.season"] = "poster"
    self.QA_FIELDS["qa.art.tvshows.episode"] = "thumb"

    self.QA_FIELDS["qa.art.pvr.tv.channel"] = "thumbnail"
    self.QA_FIELDS["qa.art.pvr.radio.channel"] = "thumbnail"

    for x in ["addons",
              "albums", "artists", "songs",
              "movies", "sets",
              "tvshows.tvshow", "tvshows.season", "tvshows.episode",
              "pvr.tv", "pvr.radio", "pvr.tv.channel", "pvr.radio.channel"]:
      key = "extrajson.%s" % x
      temp = self.getValue(config, key, "")
      self.XTRAJSON[key] = temp if temp != "" else None

      for f in ["zero", "blank", "art"]:
        key = "qa.%s.%s" % (f, x)
        try:
          temp = self.getValue(config, key, None)
        except:
          temp = None
        if temp and temp.startswith("+"):
          temp = temp[1:]
          temp2 = self.QA_FIELDS.get(key, "")
          if temp2 != "": temp2 = "%s, " % temp2
          temp = "%s%s " % (temp2, temp.strip())
          self.QA_FIELDS[key] = temp
        else:
          self.QA_FIELDS[key] = temp if temp != None else self.QA_FIELDS.get(key, None)

    self.QAPERIOD = int(self.getValue(config, "qaperiod", "30"))
    adate = datetime.date.today() - datetime.timedelta(days=self.QAPERIOD)
    self.QADATE = adate.strftime("%Y-%m-%d")

    self.QA_FILE = self.getBoolean(config, "qafile", "no")
    self.QA_FAIL_TYPES = self.getPatternFromList(config, "qa.fail.urls", embedded_urls)
    self.QA_WARN_TYPES = self.getPatternFromList(config, "qa.warn.urls", "")

    self.CACHE_CAST_THUMB = self.getBoolean(config, "cache.castthumb", "no")

    self.LOGFILE = self.getValue(config, "logfile", "")
    self.LOGVERBOSE = self.getBoolean(config, "logfile.verbose", "no")

    self.CACHE_IGNORE_TYPES = self.getPatternFromList(config, "cache.ignore.types", embedded_urls)
    self.PRUNE_RETAIN_TYPES = self.getPatternFromList(config, "prune.retain.types", "")

    self.RECACHEALL = self.getBoolean(config, "allow.recacheall","no")
    self.CHECKUPDATE = self.getBoolean(config, "checkupdate", "yes")

    self.LASTRUNFILE = self.getValue(config, "lastrunfile", "")
    self.LASTRUNFILE_DATETIME = None
    if self.LASTRUNFILE and os.path.exists(self.LASTRUNFILE):
        temp = datetime.datetime.fromtimestamp(os.path.getmtime(self.LASTRUNFILE))
        self.LASTRUNFILE_DATETIME = temp.strftime("%Y-%m-%d %H:%M:%S")

    self.ORPHAN_LIMIT_CHECK = self.getBoolean(config, "orphan.limit.check", "yes")

    self.NONMEDIA_FILETYPES = self.getSimpleList(config, "nonmedia.filetypes", "")

  def getValue(self, config, aKey, default=None):
    value = default

    try:
      value = config.get(self.THIS_SECTION, aKey)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
      if self.THIS_SECTION != self.GLOBAL_SECTION:
        try:
          value = config.get(self.GLOBAL_SECTION, aKey)
        except ConfigParser.NoOptionError:
          if default == None:
            raise ConfigParser.NoOptionError(aKey, "%s (or global section)" % self.THIS_SECTION)
      else:
        if default == None:
          raise ConfigParser.NoOptionError(aKey, self.GLOBAL_SECTION)

    return value

  # default value will be used if key is present, but without a value
  def getBoolean(self, config, aKey, default="no"):
    temp = self.getValue(config, aKey, default).lower()
    return True if (temp == "yes" or temp == "true") else False

  def getSimpleList(self, config, aKey, default=""):
    aStr = self.getValue(config, aKey, default)

    newlist = []

    if aStr:
      for item in aStr.split(","):
        newlist.append(item)

    return newlist

  def getPatternFromList(self, config, aKey, default=""):
    aList = self.getValue(config, aKey, default)

    if aList and aList.startswith("+"):
      aList = aList[1:]
      if default and default != "" and aList != "":
        aList = "%s,%s " % (default, aList.strip())

    return [re.compile(x.strip()) for x in aList.split(',')] if aList else aList

  def getListFromPattern(self, aPattern):
    if not aPattern: return None
    t = []
    for r in aPattern: t.append(r.pattern)
    return ", ".join(t)

  def getQAFields(self, qatype, mediatype, stripModifier=True):
    if mediatype in ["tvshows", "seasons", "episodes"]:
      mediatype = "tvshows.%s" % mediatype[:-1]
    elif mediatype in ["pvr.tv", "pvr.radio", "pvr.channel"]:
      mediatype = "pvr.%s" % mediatype.split(".")[1]
    elif mediatype == "tags":
      mediatype = "movies"

    key = "qa.%s.%s" % (qatype, mediatype)

    aStr = self.QA_FIELDS.get(key, None)

    newlist = []

    if aStr:
      for item in [item.strip() for item in aStr.split(",")]:
        if item != "":
          if stripModifier and item.startswith("?"):
            newlist.append(item[1:])
          else:
            newlist.append(item)

    return newlist

  def getFilePath( self, filename = "" ):
    if os.path.isabs(self.THUMBNAILS):
      return os.path.join(self.THUMBNAILS, filename)
    else:
      return os.path.join(self.XBMC_BASE, self.THUMBNAILS, filename)

  def getDBPath( self ):
    if os.path.isabs(self.TEXTUREDB):
      return self.TEXTUREDB
    else:
      return os.path.join(self.XBMC_BASE, self.TEXTUREDB)

  def NoneIsBlank(self, x):
    return x if x else ""

  def BooleanIsYesNo(self, x):
    return "yes" if x else "no"

  def showConfig(self):
    print("Current properties (if exists, read from %s%s%s):" % (os.path.dirname(__file__), os.sep, self.CONFIG_NAME))
    print("")
    print("  sep = %s" % self.FSEP)
    print("  userdata = %s " % self.XBMC_BASE)
    print("  dbfile = %s" % self.TEXTUREDB)
    print("  thumbnails = %s " % self.THUMBNAILS)
    print("  xbmc.host = %s" % self.XBMC_HOST)
    print("  webserver.port = %s" % self.WEB_PORT)
    print("  rpc.port = %s" % self.RPC_PORT)
    print("  download.threads = %d" % self.DOWNLOAD_THREADS_DEFAULT)
    if self.DOWNLOAD_THREADS != {}:
      for dt in self.DOWNLOAD_THREADS:
        if self.DOWNLOAD_THREADS[dt] != self.DOWNLOAD_THREADS_DEFAULT:
          print("  %s = %d" % (dt, self.DOWNLOAD_THREADS[dt]))
    print("  singlethread.urls = %s" % self.NoneIsBlank(self.getListFromPattern(self.SINGLETHREAD_URLS)))
    print("  extrajson.addons  = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.addons"]))
    print("  extrajson.albums  = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.albums"]))
    print("  extrajson.artists = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.artists"]))
    print("  extrajson.songs   = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.songs"]))
    print("  extrajson.movies  = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.movies"]))
    print("  extrajson.sets    = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.sets"]))
    print("  extrajson.tvshows.tvshow = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.tvshows.tvshow"]))
    print("  extrajson.tvshows.season = %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.tvshows.season"]))
    print("  extrajson.tvshows.episode= %s" % self.NoneIsBlank(self.XTRAJSON["extrajson.tvshows.episode"]))
    print("  qaperiod = %d (added after %s)" % (self.QAPERIOD, self.QADATE))
    print("  qafile = %s" % self.BooleanIsYesNo(self.QA_FILE))
    print("  qa.fail.urls = %s" % self.NoneIsBlank(self.getListFromPattern(self.QA_FAIL_TYPES)))
    print("  qa.warn.urls = %s" % self.NoneIsBlank(self.getListFromPattern(self.QA_WARN_TYPES)))

    for k in sorted(self.QA_FIELDS):
      print("  %s = %s" % (k, self.NoneIsBlank(self.QA_FIELDS[k])))

    print("  cache.castthumb = %s" % self.BooleanIsYesNo(self.CACHE_CAST_THUMB))
    print("  cache.ignore.types = %s" % self.NoneIsBlank(self.getListFromPattern(self.CACHE_IGNORE_TYPES)))
    print("  prune.retain.types = %s" % self.NoneIsBlank(self.getListFromPattern(self.PRUNE_RETAIN_TYPES)))
    print("  logfile = %s" % self.NoneIsBlank(self.LOGFILE))
    print("  logfile.verbose = %s" % self.BooleanIsYesNo(self.LOGVERBOSE))
    print("  checkupdate = %s" % self.BooleanIsYesNo(self.CHECKUPDATE))
    if self.RECACHEALL:
      print("  allow.recacheall = yes")
    temp = " (%s)" % self.LASTRUNFILE_DATETIME if self.LASTRUNFILE and self.LASTRUNFILE_DATETIME else ""
    print("  lastrunfile = %s%s" % (self.NoneIsBlank(self.LASTRUNFILE), temp))
    print("  orphan.limit.check = %s" % self.BooleanIsYesNo(self.ORPHAN_LIMIT_CHECK))
    print("  nonmedia.filetypes = %s" % self.NoneIsBlank(",".join(self.NONMEDIA_FILETYPES)))
    print("")
    print("See http://wiki.xbmc.org/index.php?title=JSON-RPC_API/v6 for details of available audio/video fields.")

#
# Very simple logging class. Will be a global object, also passed to threads
# hence the Lock() methods..
#
# Writes progress information to stderr so that
# information can still be grep'ed easily (stdout).
#
# Prefix logfilename with + to enable flushing after each write.
#
class MyLogger():
  def __init__( self ):
    self.lastlen = 0
    self.now = 0
    self.LOGGING = False
    self.LOGFILE = None
    self.LOGFLUSH = False
    self.DEBUG = False
    self.VERBOSE = False

    #Ensure stdout/stderr use utf-8 encoding...
    if sys.version_info >= (3, 1):
      sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
      sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())
    else:
      sys.stdout = codecs.getwriter("utf-8")(sys.stdout)
      sys.stderr = codecs.getwriter("utf-8")(sys.stderr)

  def __del__( self ):
    if self.LOGFILE: self.LOGFILE.close()

  def setLogFile(self, filename):
    with threading.Lock():
      if filename:
        self.LOGFLUSH = filename.startswith("+")
        if self.LOGFLUSH: filename = filename[1:]
        try:
          self.LOGFILE = codecs.open(filename, "w", encoding="utf-8")
          self.LOGGING = True
        except:
          raise IOError("Unable to open logfile for writing!")
      else:
        self.LOGGING = False
        if self.LOGFILE:
          self.LOGFILE.close()
          self.LOGFILE = None

  def progress(self, data, every=0, finalItem = False, newLine=False, noBlank=False):
    with threading.Lock():
      if every != 0 and not finalItem:
        self.now += 1
        if self.now != 1:
          if self.now <= every: return
          else: self.reset(initialValue=1)
      else:
        self.reset(initialValue=0)

      udata = self.toUnicode(data)
      ulen = len(data)
      spaces = self.lastlen - ulen
      self.lastlen = ulen

      if spaces > 0 and not noBlank:
        sys.stderr.write("%-s%*s\r" % (udata, spaces, " "))
      else:
        sys.stderr.write("%-s\r" % udata)
      if newLine:
        sys.stderr.write("\n")
        self.lastlen = 0
      sys.stderr.flush()

  def reset(self, initialValue=0):
    self.now = initialValue

  def out(self, data, newLine=False, log=False):
    with threading.Lock():
      udata = self.toUnicode(data)
      ulen = len(data)
      spaces = self.lastlen - ulen
      self.lastlen = ulen if udata.rfind("\n") == -1 else 0
      if spaces > 0:
        sys.stdout.write("%-s%*s" % (udata, spaces, " "))
      else:
        sys.stdout.write("%-s" % udata)
      if newLine:
        sys.stdout.write("\n")
        self.lastlen = 0
      sys.stdout.flush()
    if log: self.log(data)

  def debug(self, data, jsonrequest=None, every=0, newLine=False, newLineBefore=False):
    if self.DEBUG:
      with threading.Lock():
        if newLineBefore: sys.stderr.write("\n")
        self.progress("%s: %s" % (datetime.datetime.now(), data), every, newLine)
    self.log(data, jsonrequest=jsonrequest)

  def log(self, data, jsonrequest = None, maxLen=0):
    if self.LOGGING:
      with threading.Lock():
        udata = self.toUnicode(data)

        t = threading.current_thread().name
        if jsonrequest == None:
          d = udata
          if maxLen != 0 and not self.VERBOSE and len(d) > maxLen:
            d = "%s (truncated)" % d[:maxLen]
          self.LOGFILE.write("%s:%-10s: %s\n" % (datetime.datetime.now(), t, d))
        else:
          d = json.dumps(jsonrequest, ensure_ascii=True)
          if maxLen != 0 and len(d) > maxLen:
            d = "%s (truncated)" % d[:maxLen]
          self.LOGFILE.write("%s:%-10s: %s [%s]\n" % (datetime.datetime.now(), t, udata, d))
        if self.DEBUG or self.LOGFLUSH: self.LOGFILE.flush()

  def toUnicode(self, data):
    if sys.version_info >= (3, 0):
      return data

    if isinstance(data, basestring):
      if not isinstance(data, unicode):
        try:
          data = unicode(data, encoding="utf-8", errors="ignore")
        except UnicodeDecodeError:
          pass

    return data

  def removeNonAscii(self, s, replaceWith = ""):
    if replaceWith == "":
      return  "".join([x if ord(x) < 128 else ("%%%02x" % ord(x)) for x in s])
    else:
      return  "".join([x if ord(x) < 128 else replaceWith for x in s])

#
# Image loader thread class.
#
class MyImageLoader(threading.Thread):
  def __init__(self, isSingle, work_queue, other_queue, error_queue, maxItems, config, logger, totals, force=False, retry=10):
    threading.Thread.__init__(self)

    self.isSingle = isSingle

    self.work_queue = work_queue
    self.other_queue = other_queue
    self.error_queue = error_queue
    self.maxItems = maxItems

    self.config = config
    self.logger = logger
    self.database = MyDB(config, logger)
    self.json = MyJSONComms(config, logger)
    self.totals = totals

    self.force = force
    self.retry = retry

    self.LAST_URL = None

  def run(self):

    self.totals.init()

    while not stopped.is_set():
      item = self.work_queue.get()

      if not self.loadImage(item.mtype, item.itype, item.filename, item.dbid, item.cachedurl, self.retry, self.force, item.missingOK):
        if not item.missingOK:
          self.error_queue.put(item)

      self.work_queue.task_done()

      if not stopped.is_set():
        if self.isSingle:
          swqs = self.work_queue.qsize()
          mwqs = self.other_queue.qsize()
        else:
          swqs = self.other_queue.qsize()
          mwqs = self.work_queue.qsize()
        wqs = swqs + mwqs
        eqs = self.error_queue.qsize()
        tac = threading.activeCount() - 1
        self.logger.progress("Caching artwork: %d item%s remaining of %d (s: %d, m: %d), %d error%s, %d thread%s active%s" % \
                          (wqs, "s"[wqs==1:],
                           self.maxItems, swqs, mwqs,
                           eqs, "s"[eqs==1:],
                           tac, "s"[tac==1:],
                           self.totals.getPerformance(wqs)))

      if self.work_queue.empty(): break

    self.totals.stop()

  def loadImage(self, mediatype, imgtype, filename, rowid, cachedurl, retry, force, missingOK = False):

    self.totals.start(mediatype, imgtype)

    self.LAST_URL = self.json.getDownloadURL(filename)

    if self.LAST_URL != None:
      self.logger.log("Proceeding with download of URL [%s]" % self.LAST_URL)
      ATTEMPT = retry
      if rowid != 0 and force:
        self.logger.log("Deleting old image from cache with id [%d], cachedurl [%s] for filename [%s]" % (rowid, cachedurl, filename))
        self.database.deleteItem(rowid, cachedurl)
        self.totals.bump("Deleted", imgtype)
    else:
      self.logger.log("Image not available for download - uncacheable (embedded?), or doesn't exist. Filename [%s]" % filename)
      ATTEMPT = 0

    while ATTEMPT > 0:
      try:
        # Don't need to download the whole image for it to be cached so just grab the first 1KB
        PAYLOAD = self.json.sendWeb("GET", self.LAST_URL, readAmount = 1024, rawData=True)
        if self.json.WEB_LAST_STATUS == httplib.OK:
          self.logger.log("Successfully downloaded image with size [%d] bytes, attempts required [%d]. Filename [%s]" \
                        % (len(PAYLOAD), (retry - ATTEMPT + 1), filename))
          self.totals.bump("Cached", imgtype)
          break
      except:
        pass
      ATTEMPT -= 1
      self.logger.log("Failed to download image URL [%s], status [%d], " \
                   "attempts remaining [%d]" % (self.LAST_URL, self.json.WEB_LAST_STATUS, ATTEMPT))
      if stopped.is_set(): ATTEMPT = 0

    if ATTEMPT == 0:
      if not missingOK:
        self.totals.bump("Error", imgtype)

    self.totals.finish(mediatype, imgtype)

    return ATTEMPT != 0

#
# Simple database wrapper class.
#
class MyDB(object):
  def __init__(self, config, logger):
    self.config = config
    self.logger = logger

    self.mydb = None
    self.DBVERSION = None
    self.cursor = None

    self.RETRY_MAX = 5
    self.RETRY = 0

  def __enter__(self):
    self.getDB()
    return self

  def __exit__(self, atype, avalue, traceback):
    if self.cursor: self.cursor.close()
    if self.mydb: self.mydb.close()

  def getDB(self):
    if not self.mydb:
      if not os.path.exists(self.config.getDBPath()):
        raise lite.OperationalError("Database [%s] does not exist" % self.config.getDBPath())
      self.mydb = lite.connect(self.config.getDBPath(), timeout=10)
      self.mydb.text_factory = lambda x: x.decode('iso-8859-1')
      self.DBVERSION = self.execute("SELECT idVersion FROM version").fetchone()[0]
    return self.mydb

  def execute(self, SQL):
    self.cursor = self.getDB().cursor()
    self.logger.log("EXECUTING SQL: %s" % SQL)
    try:
      self.cursor.execute(SQL)
    except lite.OperationalError as e:
      if str(e) == "database is locked" and self.RETRY <= self.RETRY_MAX:
        time.sleep(0.1)
        self.RETRY += 1
        self.logger.log("EXCEPTION SQL: %s - retrying attempt #%d" % (e.value, self.RETRY))
        self.execute(SQL)
      else:
        raise

    self.RETRY = 0

    return self.cursor

  def fetchone(self): return self.cursor.fetchone()
  def fetchall(self): return self.cursor.fetchall()
  def commit(self): return self.getDB().commit()

  def getAllColumns(self, extraSQL=None):
    if self.DBVERSION >= 13:
      SQL = "SELECT t.id, t.cachedurl, t.lasthashcheck, t.url, s.height, s.width, s.usecount, s.lastusetime " \
            "FROM texture t JOIN sizes s ON (t.id = s.idtexture) "
    else:
      SQL = "SELECT t.id, t.cachedurl, t.lasthashcheck, t.url, 0 as height, 0 as width, t.usecount, t.lastusetime " \
            "FROM texture t "

    if extraSQL: SQL += extraSQL

    return self.execute(SQL)

  def deleteItem(self, id, cachedURL = None):
    if not cachedURL:
      SQL = "SELECT id, cachedurl, lasthashcheck, url FROM texture WHERE id=%d" % id
      row = self.execute(SQL).fetchone()

      if row == None:
        self.logger.out("id %s is not valid\n" % (self.config.IDFORMAT % int(id)))
        return
      else:
        localFile = row[1]
    else:
      localFile = cachedURL

    if os.path.exists(self.config.getFilePath(localFile)):
      os.remove(self.config.getFilePath(localFile))
    else:
      self.logger.out("WARNING: id %s, cached thumbnail file %s not found" % ((self.config.IDFORMAT % id), localFile), newLine=True)

    self.execute("DELETE FROM texture WHERE id=%d" % id)
    self.commit()

  def getRowByFilename(self, filename):
  # Strip image:// prefix, trailing / suffix, and unquote...
    row = self.getRowByFilename_Impl(filename[8:-1], unquote=True)

  # Didn't find anyhing so try again, this time leave filename quoted, and don't truncate
    if not row:
      self.logger.log("Failed to find row by filename with the expected formatting, trying again (with prefix, quoted)")
      row = self.getRowByFilename_Impl(filename, unquote=False)

    return row

  def getRowByFilename_Impl(self, filename, unquote=True):
    ufilename = urllib2.unquote(filename) if unquote else filename

    # If string contains unicode, replace unicode chars with % and
    # use LIKE instead of equality
    if ufilename.encode("ascii", "ignore") == ufilename.encode("utf-8"):
      SQL = "SELECT id, cachedurl from texture where url = \"%s\"" % ufilename
    else:
      self.logger.log("Removing ASCII from filename: [%s]" % ufilename)
      SQL = "SELECT id, cachedurl from texture where url like \"%s\"" % removeNonAscii(ufilename, "%")

    self.logger.log("SQL EXECUTE: [%s]" % SQL)
    row = self.execute(SQL).fetchone()
    self.logger.log("SQL RESULT : [%s]" % (row,))

    return row if row else None

  def removeNonAscii(self, s, replaceWith = ""):
    if replaceWith == "":
      return  "".join([x if ord(x) < 128 else ("%%%02x" % ord(x)) for x in s])
    else:
      return  "".join([x if ord(x) < 128 else replaceWith for x in s])

  def dumpRow(self, row):
    line= ("%s%s%14s%s%04d%s%04d%s%04d%s%19s%s%19s%s%s\n" % \
           ((self.config.IDFORMAT % row[0]), self.config.FSEP, row[1], self.config.FSEP, row[4],
             self.config.FSEP,      row[5],  self.config.FSEP, row[6], self.config.FSEP, row[7],
             self.config.FSEP,      row[2],  self.config.FSEP, row[3]))

    self.logger.out(line)

#
# Handle all JSON RPC communication.
#
# Uses sockets except for those methods (Files.*) that must
# use HTTP.
#
class MyJSONComms(object):
  def __init__(self, config, logger):
    self.config = config
    self.logger = logger
    self.mysocket = None
    self.myweb = None
    self.WEB_LAST_STATUS = -1
    self.config.WEB_SINGLESHOT = True
    self.aUpdateCount = self.vUpdateCount = 0
    self.jcomms2 = None

  def __enter__(self):
    return self

  def __exit__(self, atype, avalue, traceback):
    return

  def __del__(self):
    if self.mysocket: self.mysocket.close()
    if self.myweb: self.myweb.close()

  def getSocket(self):
    if not self.mysocket:
      self.mysocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.mysocket.connect((self.config.XBMC_HOST, int(self.config.RPC_PORT)))
    return self.mysocket

  # Use a secondary socket object for simple lookups to avoid having to handle
  # re-entrant code due to notifications being received out of sequence etc.
  # Could instantiate an object whenever required, but keeping a reference here
  # should improve effeciency slightly.
  def getLookupObject(self):
    if not self.jcomms2:
      self.jcomms2 = MyJSONComms(self.config, self.logger)
    return self.jcomms2

  def getWeb(self):
    if not self.myweb or self.config.WEB_SINGLESHOT:
      if self.myweb: self.myweb.close()
      self.myweb = httplib.HTTPConnection("%s:%s" % (self.config.XBMC_HOST, self.config.WEB_PORT))
      self.WEB_LAST_STATUS = -1
      if self.config.DEBUG: self.myweb.set_debuglevel(1)
    return self.myweb

  def sendWeb(self, request_type, url, request=None, headers={}, readAmount = 0, timeout=15.0, rawData=False):
    if self.config.WEB_AUTH_TOKEN:
      headers.update({"Authorization": "Basic %s" % self.config.WEB_AUTH_TOKEN})

    web = self.getWeb()

    web.request(request_type, url, request, headers)

    if timeout == None: web.sock.setblocking(1)
    else: web.sock.settimeout(timeout)

    try:
      response = web.getresponse()
      self.WEB_LAST_STATUS = response.status
      if self.WEB_LAST_STATUS == httplib.UNAUTHORIZED:
        raise httplib.HTTPException("Remote web host requires webserver.username/webserver.password properties")
      if sys.version_info >= (3, 0) and not rawData:
        if readAmount == 0: return response.read().decode("utf-8")
        else: return response.read(readAmount).decode("utf-8")
      else:
        if readAmount == 0: return response.read()
        else: return response.read(readAmount)
    except socket.timeout:
      self.logger.log("** iotimeout occurred during web request **")
      self.WEB_LAST_STATUS = httplib.REQUEST_TIMEOUT
      self.myweb.close()
      self.myweb = None
      return ""
    except:
      if self.config.WEB_SINGLESHOT == False:
        self.logger.log("SWITCHING TO WEBSERVER.SINGLESHOT MODE")
        self.config.WEB_SINGLESHOT = True
        return self.sendWeb(request_type, url, request, headers)
      raise

  def sendJSON(self, request, id, callback=None, timeout=5.0, checkResult=True):
    BUFFER_SIZE = 32768

    request["jsonrpc"] = "2.0"
    request["id"] =  id

    # Following methods don't work over sockets - by design.
    if request["method"] in ["Files.PrepareDownload", "Files.Download"]:
      self.logger.log("%s.JSON WEB REQUEST:" % id, jsonrequest=request)
      data = self.sendWeb("POST", "/jsonrpc", json.dumps(request), {"Content-Type": "application/json"})
      if self.logger.LOGGING:
        self.logger.log("%s.RECEIVED DATA: %s" % (id, data), maxLen=256)
      return json.loads(data) if data != "" else ""

    s = self.getSocket()
    self.logger.log("%s.JSON SOCKET REQUEST:" % id, jsonrequest=request)
    START_IO_TIME=time.time()

    if sys.version_info >= (3, 0):
      s.send(bytes(json.dumps(request), "utf-8"))
    else:
      s.send(json.dumps(request))

    ENDOFDATA = True
    LASTIO = 0
    jdata = {}

    while True:
      if ENDOFDATA:
        ENDOFDATA = False
        s.setblocking(1)
        data = b""

      try:
        newdata = s.recv(BUFFER_SIZE)
        if len(data) == 0: s.settimeout(1.0)
        data += newdata
        LASTIO=time.time()
        self.logger.log("%s.BUFFER RECEIVED (len %d)" % (id, len(newdata)))
        READ_ERR = False
      except socket.error as e:
        READ_ERR = True

      # Keep reading unless accumulated data is a likely candidate for successful parsing...
      if not READ_ERR and len(data) != 0 and (data[-1:] == b"}" or data[-2:] == b"}\n"):

        # If data is not a str (Python2) then decode Python3 bytes to unicode representation
        if isinstance(data, str):
          udata = self.logger.toUnicode(data)
        else:
          try:
            udata = data.decode("utf-8")
          except UnicodeDecodeError as e:
            continue

        try:
          if self.logger.LOGGING: self.logger.log("%s.PARSING JSON DATA: %s" % (id, udata), maxLen=256)

          START_PARSE_TIME = time.time()

          # Parse messages, to ensure the entire buffer is valid
          # If buffer is not valid (VALUE EXCEPTION), we may need to read more data.
          messages = []
          for m in self.parseResponse(udata):
            messages.append(m)

          self.logger.log("%s.PARSING COMPLETE, elapsed time: %f seconds" % (id, time.time() - START_PARSE_TIME))

          # Process any notifications first.
          # Any message with an id must be processed after notification - should only be one at most...
          result = False
          jdata = {}
          for m in messages:
            if not "id" in m:
              if callback:
                if self.handleResponse(id, m, callback):
                  result = True
              elif self.logger.LOGGING:
                self.logger.log("%s.IGNORING NOTIFICATION" % id, jsonrequest=m, maxLen=256)
            elif m["id"] == id:
              jdata = m

          messages = []

          if ("result" in jdata and "limits" in jdata["result"]):
            self.logger.log("%s.RECEIVED LIMITS: %s" % (id, jdata["result"]["limits"]))

          # Flag to reset buffers next time we read the socket.
          ENDOFDATA = True

          # callback result for a comingled Notification - stop blocking/reading and
          # return to caller with response (jdata)
          if result: break

          # Got a response...
          if jdata != {}:
            # If callback defined, pass it the message then break if result is True.
            # Otherwise break only if message has an id, that is to
            # say, continue reading data (blocking) until a message (response)
            # with an id is available.
            if callback:
              if self.handleResponse(id, jdata, callback): break
            elif "id" in jdata:
              break

          if callback:
            self.logger.log("%s.READING SOCKET UNTIL CALLBACK SUCCEEDS..." % id)
          else:
            self.logger.log("%s.READING SOCKET FOR A RESPONSE..." % id)

        except ValueError as e:
          # If we think we've reached EOF (no more data) and we have invalid data then
          # raise exception,  otherwise continue reading more data
          if READ_ERR:
            self.logger.log("%s.VALUE ERROR EXCEPTION: %s" % (id, str(e)))
            raise
          else:
            self.logger.log("%s.Incomplete JSON data - continue reading socket" % id)
            if self.logger.VERBOSE: self.logger.log("Ignored Value Error: %s" % e)
            continue
        except Exception as e:
          self.logger.log("%s.GENERAL EXCEPTION: %s" % (id, str(e)))
          raise

      # Still more data to be read...
      if not ENDOFDATA:
        if (time.time() - LASTIO) > timeout:
          self.logger.log("SOCKET IO TIMEOUT EXCEEDED")
          raise socket.error("Socket IO timeout exceeded")

    if checkResult and not "result" in jdata:
      self.logger.out("%s.ERROR: JSON response has no result!\n%s\n" % (id, jdata))

    self.logger.log("%s.FINISHED, elapsed time: %f seconds" % (id, time.time() - START_IO_TIME))
    return jdata

  # Split data into individual json objects.
  def parseResponse(self, data):

    decoder = json._default_decoder
    _w=json.decoder.WHITESPACE.match

    idx = _w(data, 0).end()
    end = len(data)

    try:
      while idx != end:
        (val, idx) = decoder.raw_decode(data, idx=idx)
        yield val
        idx = _w(data, idx).end()
    except ValueError as exc:
      raise ValueError('%s (%r at position %d).' % (exc, data[idx:], idx))

  # Process Notifications, optionally executing a callback function for
  # additional custom processing.
  def handleResponse(self, callingId, jdata, callback):
    id = jdata["id"] if "id" in jdata else None
    method = jdata["method"] if "method" in jdata else jdata["result"]
    params = jdata["params"] if "params" in jdata else None

    if callback:
      cname = callback.__name__
      self.logger.log("%s.PERFORMING CALLBACK: Name [%s], with Id [%s], Method [%s], Params [%s]" % (callingId, cname, id, method, params))
      result =  callback(id, method, params)
      self.logger.log("%s.CALLBACK RESULT: [%s] Name [%s], Id [%s], Method [%s], Params [%s]" % (callingId, result, cname, id, method, params))
      return result

    return False

  def listen(self):
    REQUEST = {"method": "JSONRPC.Ping"}
    self.sendJSON(REQUEST, "libListen", callback=self.speak, checkResult=False)

  def speak(self,id, method, params):
    # Only interested in Notifications...
    if id: return False

    item = None
    title = None
    pmsg = None

    if params["data"]:
      pmsg = json.dumps(params["data"])
      if type(params["data"]) is dict:
        if "item" in params["data"]:
          item = params["data"]["item"]
        elif "type" in params["data"]:
          item = params["data"]

      if item:
        title = self.getTitleForLibraryItem(item.get("type", None), item.get("id", None))

    if title:
      self.logger.out("%s: %-21s: %s [%s]" % (datetime.datetime.now(), method, pmsg, title), newLine=True)
    else:
      self.logger.out("%s: %-21s: %s" % (datetime.datetime.now(), method, pmsg), newLine=True)

    return True if method == "System.OnQuit" else False

  def jsonWaitForScanFinished(self, id, method, params):
    if method.endswith("Library.OnUpdate") and "data" in params:
      if method == "AudioLibrary.OnUpdate": self.aUpdateCount += 1
      if method == "VideoLibrary.OnUpdate": self.vUpdateCount += 1

      if "item" in params["data"]:
        item = params["data"]["item"]
      elif "type" in params["data"]:
        item = params["data"]
      else:
        item = None

      if item:
        iType = item["type"]
        libraryId = item["id"]
        title = self.getTitleForLibraryItem(iType, libraryId)

        if title:
          self.logger.out("Updating Library: New %-9s %5d [%s]\n" % (iType + "id", libraryId, title))
        else:
          self.logger.out("Updating Library: New %-9s %5d\n" % (iType + "id", libraryId))

    return True if method.endswith("Library.OnScanFinished") else False

  def jsonWaitForCleanFinished(self, id, method, params):
    return True if method.endswith("Library.OnCleanFinished") else False

  def addProperties(self, request, fields):
    if not "properties" in request["params"]: return
    aList = request["params"]["properties"]
    if fields != None:
      for f in [f.strip() for f in fields.split(",")]:
        if f != "" and not f in aList:
          aList.append(f)
    request["params"]["properties"] = aList

  def addFilter(self, request, newFilter, condition="and"):
    filter = request["params"]
    if "filter" in filter:
       filter["filter"] = { condition: [ filter["filter"], newFilter ] }
    else:
       filter["filter"] = newFilter
    request["params"] = filter

  def rescanDirectories(self, workItems):
    if workItems == {}: return

    # Seems to be a bug in rescan method when scanning the root folder of a source
    # So if any items are in the root folder, just scan the entire library after
    # items have been removed.
    rootScan = False
    sources = self.getSources("video")

    for directory in sorted(workItems):
      (mediatype, dpath) = directory.split(";")
      if dpath in sources: rootScan = True

    for directory in sorted(workItems):
      (mediatype, dpath) = directory.split(";")

      for disc_folder in [ ".BDMV$", ".VIDEO_TS$" ]:
        re_match = re.search(disc_folder, dpath, flags=re.IGNORECASE)
        if re_match:
          dpath = dpath[:re_match.start()]
          break

      if mediatype == "movies":
        scanMethod = "VideoLibrary.Scan"
        removeMethod = "VideoLibrary.RemoveMovie"
        idName = "movieid"
      elif mediatype == "tvshows":
        scanMethod = "VideoLibrary.Scan"
        removeMethod = "VideoLibrary.RemoveTVShow"
        idName = "tvshowid"
      elif mediatype == "episodes":
        scanMethod = "VideoLibrary.Scan"
        removeMethod = "VideoLibrary.RemoveEpisode"
        idName = "episodeid"
      else:
        raise ValueError("mediatype [%s] not yet implemented" % mediatype)

      for libraryid in workItems[directory]:
        self.logger.log("Removing %s %d from media library." % (idName, libraryid))
        REQUEST = {"method": removeMethod, "params":{idName: libraryid}}
        self.sendJSON(REQUEST, "libRemove")

      if not rootScan: self.scanDirectory(scanMethod, path=dpath)

    if rootScan: self.scanDirectory(scanMethod)

  def scanDirectory(self, scanMethod, path=None):
    if path and path != "":
      self.logger.out("Rescanning directory: %s..." % path, newLine=True, log=True)
      REQUEST = {"method": scanMethod, "params":{"directory": path}}
    else:
      self.logger.out("Rescanning library...", newLine=True, log=True)
      REQUEST = {"method": scanMethod, "params":{"directory": ""}}

    self.sendJSON(REQUEST, "libRescan", callback=self.jsonWaitForScanFinished, checkResult=False)

  def cleanLibrary(self, cleanMethod):
    self.logger.out("Cleaning library...", newLine=True, log=True)
    REQUEST = {"method": cleanMethod}
    self.sendJSON(REQUEST, "libClean", callback=self.jsonWaitForCleanFinished, checkResult=False)

  def getDirectoryList(self, mediatype, path):
    REQUEST = {"method":"Files.GetDirectory",
               "params": {"directory": path, "media": mediatype},
               "properties": ["file", "art", "fanart", "thumb", "size", "dateadded", "lastmodified", "mimetype"]}
    return self.sendJSON(REQUEST, "libDirectory", checkResult=False)

  def getSeasonAll(self, filename):

    # Not able to get a directory for remote files...
    if filename.find("image://http") != -1: return (None, None, None)

    directory = urllib2.unquote(filename[8:-1])

    # Remove filename, leaving just directory...
    ADD_BACK=""
    if directory.rfind("/") != -1:
      directory = directory[:directory.rfind("/")]
      ADD_BACK="/"
    if directory.rfind("\\") != -1:
      directory = directory[:directory.rfind("\\")]
      ADD_BACK="\\"

    REQUEST = {"method":"Files.GetDirectory", "params":{"directory": directory}}

    data = self.sendJSON(REQUEST, "libDirectory", checkResult=False)

    if "result" in data and "files" in data["result"]:
      poster_url = fanart_url = banner_url = None
      for f in data["result"]["files"]:
        if f["filetype"] == "file":
          fname = f["label"].lower()
          if fname.find("season-all-poster.") != -1: poster_url = "image://%s%s" % (urllib2.quote(f["file"], "()"),ADD_BACK)
          elif not poster_url and fname.find("season-all.") != -1: poster_url = "image://%s%s" % (urllib2.quote(f["file"], "()"),ADD_BACK)
          elif fname.find("season-all-banner.") != -1: banner_url = "image://%s%s" % (urllib2.quote(f["file"], "()"),ADD_BACK)
          elif fname.find("season-all-fanart.") != -1: fanart_url = "image://%s%s" % (urllib2.quote(f["file"], "()"),ADD_BACK)
      return (poster_url, fanart_url, banner_url)

    return (None, None, None)

  def getDownloadURL(self, filename):
    REQUEST = {"method":"Files.PrepareDownload",
               "params":{"path": filename }}

    data = self.sendJSON(REQUEST, "preparedl")

    if "result" in data:
      return "/%s" % data["result"]["details"]["path"]
    else:
      if filename[8:12].lower() != "http":
        self.logger.log("Files.PrepareDownload failed. It's a local file, what the heck... trying anyway.")
        return "/image/%s" % urllib2.quote(filename, "()")
      return None

  def getFileDetails(self, filename):
    REQUEST = {"method":"Files.GetFileDetails",
               "params":{"file": filename,
                         "properties": ["streamdetails", "lastmodified", "dateadded", "size", "mimetype", "tag", "file"]}}

    data = self.sendJSON(REQUEST, "filedetails", checkResult=False)

    if "result" in data:
      return data["result"]["filedetails"]
    else:
      return None

  # Get title of item - usually during a notification. As this can be
  # an OnRemove notification, don't check for result as the item may have
  # been removed before it can be looked up, in which case return None.
  def getTitleForLibraryItem(self, iType, libraryId):
    title = None

    if iType and libraryId:
      # Use the secondary socket object to avoid consuming
      # notifications that are meant for the caller.
      if iType == "song":
        title = self.getLookupObject().getSongName(libraryId)
      elif iType == "movie":
        title = self.getLookupObject().getMovieName(libraryId)
      elif iType == "tvshow":
        title = self.getLookupObject().getTVShowName(libraryId)
      elif iType == "episode":
        title = self.getLookupObject().getEpisodeName(libraryId)

    return title

  def getSongName(self, songid):
    REQUEST = {"method":"AudioLibrary.GetSongDetails",
               "params":{"songid": songid, "properties":["title", "artist", "albumartist"]}}
    data = self.sendJSON(REQUEST, "libSong", checkResult=False)
    if "result" in data and "songdetails" in data["result"]:
      s = data["result"]["songdetails"]
      if s["artist"]:
        return "%s (%s)" % (s["title"], "/".join(s["artist"]))
      else:
        return "%s (%s)" % (s["title"], "/".join(s["albumartist"]))
    else:
      return None

  def getTVShowName(self, tvshowid):
    REQUEST = {"method":"VideoLibrary.GetTVShowDetails",
               "params":{"tvshowid": tvshowid, "properties":["title"]}}
    data = self.sendJSON(REQUEST, "libTVShow", checkResult=False)
    if "result" in data and "tvshowdetails" in data["result"]:
      t = data["result"]["tvshowdetails"]
      return "%s" % t["title"]
    else:
      return None

  def getEpisodeName(self, episodeid):
    REQUEST = {"method":"VideoLibrary.GetEpisodeDetails",
               "params":{"episodeid": episodeid, "properties":["title", "showtitle", "season", "episode"]}}
    data = self.sendJSON(REQUEST, "libEpisode", checkResult=False)
    if "result" in data and "episodedetails" in data["result"]:
      e = data["result"]["episodedetails"]
      return "%s S%02dE%02d (%s)" % (e["showtitle"], e["season"], e["episode"], e["title"])
    else:
      return None

  def getMovieName(self, movieid):
    REQUEST = {"method":"VideoLibrary.GetMovieDetails",
               "params":{"movieid": movieid, "properties":["title"]}}
    data = self.sendJSON(REQUEST, "libMovie", checkResult=False)
    if "result" in data and "moviedetails" in data["result"]:
      m = data["result"]["moviedetails"]
      return "%s" % m["title"]
    else:
      return None

  def dumpJSON(self, data, decode=False):
    if decode:
      self.logger.progress("Decoding URLs...")
      self.unquoteArtwork(data)
    self.logger.progress("")
    self.logger.out(json.dumps(data, indent=2, ensure_ascii=True, sort_keys=False), newLine=True)

  def unquoteArtwork(self, items):
    for item in items:
      for field in item:
        if field in ["seasons", "episodes", "channels"]: self.unquoteArtwork(item[field])

        if field in ["fanart", "thumbnail"]: item[field] = urllib2.unquote(item[field])

        if field == "art":
          art = item["art"]
          for image in art:
            art[image] = urllib2.unquote(art[image])

        if field == "cast":
          for cast in item["cast"]:
            if "thumbnail" in cast:
              cast["thumbnail"] = urllib2.unquote(cast["thumbnail"])

  def getSources(self, media, labelPrefix=False, withLabel=None):
    REQUEST = {"method": "Files.GetSources", "params":{"media": media}}

    data = self.sendJSON(REQUEST, "libSources")

    source_list = []

    if "result" in data and "sources" in data["result"]:
      for source in data["result"]["sources"]:
        file = source["file"]
        label = source["label"]
        if not withLabel or withLabel.lower() == label.lower():
          if file.startswith("multipath://"):
            for qfile in file[12:].split("/"):
              if qfile != "":
                if labelPrefix:
                  source_list.append("%s: %s" % (label, urllib2.unquote(qfile)[:-1]))
                else:
                  source_list.append(urllib2.unquote(qfile)[:-1])
          else:
            if labelPrefix:
              source_list.append("%s: %s" % (label, file[:-1]))
            else:
              source_list.append(file[:-1])

    return sorted(source_list)

  def getAllFilesForSource(self, mediatype, labels):
    if mediatype == "songs":
      mtype = "music"
    else:
      mtype = "video"

    # Mostly image, nfo and audio-related playlist file types,
    # but also some random junk...
    ignoreList = [".jpg", ".png", ".nfo", ".tbn", ".srt", ".sub", ".idx", \
                  ".m3u", ".pls", ".cue", \
                  ".log", ".ini", ".txt", \
                  ".bak", ".info", ".db", ".gz", ".tar", ".rar", ".zip"]

    for extension in self.config.NONMEDIA_FILETYPES:
      if extension.startswith("."):
        ignoreList.append("%s" % extension.lower())
      else:
        ignoreList.append(".%s" % extension.lower())

    fileList = []

    for label in labels:
      sources = self.getSources(mtype, withLabel=label)

      for path in sources:
        self.logger.progress("Walking source: [%s]" % path)

        for file in self.getFilesForPath(path):
          ext = os.path.splitext(file)[1].lower()
          if ext in ignoreList: continue

          if os.path.splitext(file)[0].lower().endswith("trailer"): continue

          isVIDEOTS = (file.find("/VIDEO_TS/") != -1 or file.find("\\VIDEO_TS\\") != -1)
          isBDMV    = (file.find("/BDMV/") != -1 or file.find("\\BDMV\\") != -1)

          if isVIDEOTS and ext != ".vob": continue
          if isBDMV    and ext != ".m2ts": continue

          # Avoiding adding file to list more than once, which is possible
          # if a folder appears within multiple different sources, or the
          # same source is processed more than once...
          if not file in fileList:
            fileList.append(file)

    return sorted(fileList)

  def getFilesForPath(self, path):
    fileList = []
    self.getFilesForPath_recurse(fileList, path)
    return fileList

  def getFilesForPath_recurse(self, fileList, path):
    data = self.getDirectoryList("files", path)
    if not "result" in data: return

    files = data["result"]["files"]
    if not files: return

    for file in files:
      ftype = file["filetype"]
      fname = file["file"]
      fext = os.path.splitext(fname)[1].lower()
      #Real directories won't have extensions, but .m3u and .pls playlists will
      #leading to infinite recursion, so don't try to traverse playlists
      if ftype == "directory" and fext == "":
        self.getFilesForPath_recurse(fileList, os.path.dirname(fname))
      else:
        fileList.append(fname)

  def setPower(self, state):
    REQUEST = {"method": "System.%s" % state.capitalize()}
    data = self.sendJSON(REQUEST, "libPower")

  def getData(self, action, mediatype,
              filter = None, useExtraFields = False, secondaryFields = None,
              showid = None, seasonid = None, channelgroupid = None, lastRun = False):

    XTRA = mediatype
    SECTION = mediatype
    FILTER = "title"
    TITLE = "title"
    IDENTIFIER = "%sid" % re.sub("(.*)s$", "\\1", mediatype)

    if mediatype == "addons":
      REQUEST = {"method":"Addons.GetAddons",
                 "params":{"properties":["name", "version", "thumbnail", "fanart"]}}
      FILTER = "name"
      TITLE = "name"
    elif mediatype in ["pvr.tv", "pvr.radio"]:
      REQUEST = {"method":"PVR.GetChannelGroups",
                 "params":{"channeltype": mediatype.split(".")[1]}}
      SECTION = "channelgroups"
      FILTER = "channeltype"
      TITLE = "label"
      IDENTIFIER = "channelgroupid"
    elif mediatype in ["pvr.tv.channel", "pvr.radio.channel"]:
      REQUEST = {"method":"PVR.GetChannels",
                 "params":{"channelgroupid": channelgroupid,
                           "properties": ["channeltype", "channel", "thumbnail", "hidden", "locked", "lastplayed"]}}
      SECTION = "channels"
      FILTER = "channel"
      TITLE = "channel"
      IDENTIFIER = "channelid"
    elif mediatype == "albums":
      REQUEST = {"method":"AudioLibrary.GetAlbums",
                 "params":{"sort": {"order": "ascending", "method": "label"},
                           "properties":["title", "artist", "fanart", "thumbnail"]}}
      FILTER = "album"
    elif mediatype == "artists":
      REQUEST = {"method":"AudioLibrary.GetArtists",
                 "params":{"sort": {"order": "ascending", "method": "artist"},
                           "albumartistsonly": False,
                           "properties":["fanart", "thumbnail"]}}
      FILTER = "artist"
      TITLE = "artist"
    elif mediatype == "songs":
      REQUEST = {"method":"AudioLibrary.GetSongs",
                 "params":{"sort": {"order": "ascending", "method": "title"},
                           "properties":["title", "artist", "fanart", "thumbnail"]}}
    elif mediatype in ["movies", "tags"]:
      REQUEST = {"method":"VideoLibrary.GetMovies",
                 "params":{"sort": {"order": "ascending", "method": "title"},
                           "properties":["title", "art"]}}
      XTRA = "movies"
      SECTION = "movies"
      IDENTIFIER = "movieid"
    elif mediatype == "sets":
      REQUEST = {"method":"VideoLibrary.GetMovieSets",
                 "params":{"sort": {"order": "ascending", "method": "title"},
                           "properties":["title", "art"]}}
      FILTER = ""
    elif mediatype == "tvshows":
      REQUEST = {"method":"VideoLibrary.GetTVShows",
                 "params":{"sort": {"order": "ascending", "method": "title"},
                           "properties":["title", "art"]}}
      XTRA = "tvshows.tvshow"
    elif mediatype == "seasons":
      REQUEST = {"method":"VideoLibrary.GetSeasons",
                 "params":{"sort": {"order": "ascending", "method": "season"},
                           "tvshowid": showid, "properties":["season", "art"]}}
      FILTER = ""
      TITLE = "label"
      XTRA = "tvshows.season"
      IDENTIFIER = "season"
    elif mediatype == "episodes":
      REQUEST = {"method":"VideoLibrary.GetEpisodes",
                 "params":{"sort": {"order": "ascending", "method": "label"},
                           "tvshowid": showid, "season": seasonid, "properties":["art"]}}
      FILTER = ""
      TITLE = "label"
      XTRA = "tvshows.episode"
    else:
      raise ValueError("Invalid mediatype: [%s]" % mediatype)

    if mediatype == "tags":
        if not filter or filter.strip() == "":
          self.addFilter(REQUEST, {"field": "tag", "operator": "contains", "value": "%"})
        else:
          word = 0
          filterBoolean = "and"
          for tag in [x.strip() for x in re.split("( and | or )", filter)]:
            word += 1
            if (word%2 == 0) and tag in ["and","or"]: filterBoolean = tag
            else: self.addFilter(REQUEST, {"field": "tag", "operator": "contains", "value": tag}, filterBoolean)
    elif filter and filter.strip() != "" and not mediatype in ["addons", "sets", "seasons", "episodes", "pvr.tv", "pvr.radio", "pvr.channels"]:
        self.addFilter(REQUEST, {"field": FILTER, "operator": "contains", "value": filter})

    if mediatype in ["movies", "tags", "episodes"]:
      if lastRun and self.config.LASTRUNFILE_DATETIME:
        self.addFilter(REQUEST, {"field": "dateadded", "operator": "after", "value": self.config.LASTRUNFILE_DATETIME })

    if action == "missing":
      for unwanted in ["artist", "art", "fanart", "thumbnail"]:
        if unwanted in REQUEST["params"]["properties"]:
          REQUEST["params"]["properties"].remove(unwanted)
      if mediatype in ["songs", "movies", "tvshows", "episodes" ]:
        self.addProperties(REQUEST, "file")

    if action == "qa":
      qaSinceDate = self.config.QADATE
      if qaSinceDate and mediatype in ["movies", "tags", "episodes"]:
          self.addFilter(REQUEST, {"field": "dateadded", "operator": "after", "value": qaSinceDate })

      if mediatype in ["songs", "movies", "tags", "tvshows", "episodes" ]:
        self.addProperties(REQUEST, "file")

      self.addProperties(REQUEST, ", ".join(self.config.getQAFields("zero", XTRA)))
      self.addProperties(REQUEST, ", ".join(self.config.getQAFields("blank", XTRA)))
    elif action == "dump":
      xtraFields = self.config.XTRAJSON["extrajson.%s" % XTRA] if XTRA != "" else None
      if useExtraFields and xtraFields:
        self.addProperties(REQUEST, xtraFields)
      if secondaryFields:
        self.addProperties(REQUEST, secondaryFields)
    elif action == "query" and not mediatype in ["tvshows", "seasons", "pvr.tv", "pvr.radio"]:
      if secondaryFields:
        self.addProperties(REQUEST, secondaryFields)
    elif action == "cache":
      if mediatype in ["movies", "tags", "tvshows", "episodes"] and self.config.CACHE_CAST_THUMB:
        self.addProperties(REQUEST, "cast")

    return (SECTION, TITLE, IDENTIFIER, self.sendJSON(REQUEST, "lib%s" % mediatype.capitalize()))

#
# Hold and print some pretty totals.
#
class MyTotals(object):
  def __init__(self, lastRunDateTime):
    self.LASTRUNDATETIME = lastRunDateTime

    self.TIMES = {}

    self.ETIMES = {}

    self.THREADS = {}
    self.THREADS_HIST = {}
    self.HISTORY = []
    self.PCOUNT = self.PMIN = self.PAVG = self.PMAX = 0

    self.TOTALS = {}
    self.TOTALS["Skipped"] = {}
    self.TOTALS["Deleted"] = {}
    self.TOTALS["Duplicate"] = {}
    self.TOTALS["Error"] = {}
    self.TOTALS["Cached"] = {}
    self.TOTALS["Ignored"] = {}

  def addSeasonAll(self):
    if not "Season-all" in self.TOTALS:
      self.TOTALS["Season-all"] = {}

  def addNotCached(self):
    if not "Not in Cache" in self.TOTALS:
      self.TOTALS["Not in Cache"] = {}

  def TimeStart(self, mediatype, item):
    if not mediatype in self.TIMES: self.TIMES[mediatype] = {}
    self.TIMES[mediatype][item] = (time.time(), 0)

  def TimeEnd(self, mediatype, item):
    self.TIMES[mediatype][item] = (self.TIMES[mediatype][item][0], time.time())

  def TimeDuration(self, item):
    tElapsed = 0
    for m in self.TIMES:
      for i in self.TIMES[m]:
        if i == item:
          tuple = self.TIMES[m][i]
          tElapsed += (tuple[1] - tuple[0])
    return tElapsed

  def gotTimeDuration(self, item):
    for m in self.TIMES:
      for i in self.TIMES[m]:
        if i == item:
          return True
    return False

  def init(self):
    with threading.Lock():
      tname = threading.current_thread().name
      self.THREADS[tname] = 0
      self.THREADS_HIST[tname] = (0, 0)

  # Record start time for an image type.
  def start(self, mediatype, imgtype):
    with threading.Lock():
      tname = threading.current_thread().name
      ctime = time.time()
      self.THREADS[tname] = ctime
      if not mediatype in self.ETIMES: self.ETIMES[mediatype] = {}
      if not imgtype in self.ETIMES[mediatype]: self.ETIMES[mediatype][imgtype] = {}
      if not tname in self.ETIMES[mediatype][imgtype]: self.ETIMES[mediatype][imgtype][tname] = (ctime, 0)

  # Record current time for imgtype - this will allow stats to
  # determine cumulative time taken to download an image type.
  def finish(self, mediatype, imgtype):
    with threading.Lock():
      tname = threading.current_thread().name
      ctime = time.time()
      self.THREADS_HIST[tname] = (self.THREADS[tname], ctime)
      self.THREADS[tname] = 0
#      if mediatype in self.ETIMES and imgtype in self.ETIMES[mediatype] and tname in self.ETIMES[mediatype][imgtype]:
      self.ETIMES[mediatype][imgtype][tname] = (self.ETIMES[mediatype][imgtype][tname][0], ctime)

  def stop(self):
    self.init()

  # Increment counter for action/imgtype pairing
  def bump(self, action, imgtype):
    with threading.Lock():
      if not action in self.TOTALS: self.TOTALS[action] = {}
      if not imgtype in self.TOTALS[action]: self.TOTALS[action][imgtype] = 0
      self.TOTALS[action][imgtype] += 1

  # Calculate average performance per second.
  # Record history of averages to use as a basic smoothing function
  # Calculate and store min/max/avg peak performance.
  def getPerformance(self, remaining):

    active = tmin = tmax = 0

    with threading.Lock():
      for t in self.THREADS_HIST:
        times = self.THREADS_HIST[t]
        if times[0] != 0:
          active += 1
          if tmin == 0 or times[0] < tmin: tmin = times[0]
          if times[1] > tmax: tmax = times[1]

      if tmax == 0: return ""

      tpersec = active / (tmax - tmin)

      self.PCOUNT += 1
      self.PAVG += tpersec
      if self.PMIN == 0 or tpersec < self.PMIN: self.PMIN = tpersec
      if tpersec > self.PMAX: self.PMAX = tpersec

      # Maintain history of times to smooth out performance result...
      self.HISTORY.insert(0,tpersec)
      if len(self.HISTORY) > 25: self.HISTORY.pop()
      tpersec = 0
      for t in self.HISTORY: tpersec += t
      tpersec = tpersec/len(self.HISTORY)

    eta = self.secondsToTime(remaining / tpersec, withMillis=False)
    return " (%05.2f downloads per second, ETA: %s)" % (tpersec, eta)

  def libraryStats(self, item="", multi=[], filter="", lastRun=False, query=""):
    if multi: item = "/".join(multi)

    # Determine the artwork types that have been accumulated
    items = {}
    for a in self.TOTALS:
      for c in self.TOTALS[a]:
        if not c in items: items[c] = None

    # Ensure some basic items are included in the summary
    if item.find("pvr.") != -1:
      if not "thumbnail" in items: items["thumbnail"] = None
    else:
      if not "fanart" in items: items["fanart"] = None
    if item.find("movies") != -1:
      if not "poster" in items: items["poster"] = None
    if item.find("tvshows") != -1:
      if not "thumb" in items: items["thumb"] = None
    if item.find("artists") != -1 or \
       item.find("albums") != -1 or \
       item.find("songs") != -1:
      if not "thumbnail" in items: items["thumbnail"] = None

    DOWNLOAD_LABEL = "Download Time"

    sortedItems = sorted(items.items())
    sortedItems.append(("TOTAL", None))
    items["TOTAL"] = 0

    sortedTOTALS = sorted(self.TOTALS.items())
    sortedTOTALS.append(("TOTAL", {}))
    self.TOTALS["TOTAL"] = {}

    if len(self.THREADS_HIST) != 0:
      sortedTOTALS.append((DOWNLOAD_LABEL, {}))
      self.TOTALS[DOWNLOAD_LABEL] = {"TOTAL": 0}

    # Transfer elapsed times for each image type to our matrix of values
    # Times are held by mediatype, so accumulate for each mediatype
    # Total Download Time is sum of elapsed time for each mediatype
      self.TOTALS[DOWNLOAD_LABEL]["TOTAL"] = 0
      for mtype in self.ETIMES:
        tmin = tmax = 0.0
        for itype in self.ETIMES[mtype]:
          itmin = itmax = 0.0
          for tname in self.ETIMES[mtype][itype]:
            tuple = self.ETIMES[mtype][itype][tname]
            if tuple[0] < itmin or itmin == 0.0: itmin = tuple[0]
            if tuple[1] > itmax: itmax = tuple[1]
            if not itype in self.TOTALS[DOWNLOAD_LABEL]: self.TOTALS[DOWNLOAD_LABEL][itype] = 0
          self.TOTALS[DOWNLOAD_LABEL][itype] = (itmax - itmin)
          if itmin < tmin or tmin == 0.0: tmin = itmin
          if itmax > tmax: tmax = itmax
        self.TOTALS[DOWNLOAD_LABEL]["TOTAL"] += (tmax - tmin)

    line0 = "Cache pre-load activity summary for \"%s\"" % item
    if filter != "": line0 = "%s, filtered by \"%s\"" % (line0, filter)
    if lastRun and self.LASTRUNDATETIME: line0 = "%s, added since %s" % (line0, self.LASTRUNDATETIME)
    line0 = "%s:" % line0

    line1 = "%-14s" % " "
    line2 = "-" * 14
    for i in sortedItems:
      i = i[1] if i[1] else i[0]
      width = 12 if len(i) < 12 else len(i)+1
      line1 = "%s| %s" % (line1, i.center(width))
      line2 = "%s+-%s" % (line2, "-" * width)

    print("")
    print(line0)
    print("")
    print(line1)
    print(line2)

    for a in sortedTOTALS:
      a = a[0]
      if a != DOWNLOAD_LABEL: self.TOTALS[a]["TOTAL"] = 0
      if a == "TOTAL": print(line2.replace("-","=").replace("+","="))
      line = "%-13s " % a
      for i in sortedItems:
        i = i[0]
        if a == "TOTAL":
          value = "%d" % items[i] if items[i] != None else "-"
        elif a == DOWNLOAD_LABEL:
          if i in self.TOTALS[a] and self.TOTALS[a][i] != 0:
            value = self.secondsToTime(self.TOTALS[a][i])
          else:
            value = "-"
        elif i in self.TOTALS[a]:
          ivalue = self.TOTALS[a][i]
          value = "%d" % ivalue
          if items[i] == None: items[i] = 0
          items[i] += ivalue
          self.TOTALS[a]["TOTAL"] += ivalue
        else:
          value = "-"
        width = 12 if len(i) < 12 else len(i)+1
        line = "%s| %s" % (line, value.center(width))
      print(line)

    print("")
    self.libraryStatsSummary()

  def libraryStatsSummary(self):
    # Failed to load anything so don't display time stats that we don't have
    if not self.gotTimeDuration("Load"): return

    if len(self.THREADS_HIST) != 0:
      print("  Threads Used: %d" % len(self.THREADS_HIST))
      print("   Min/Avg/Max: %3.2f / %3.2f / %3.2f" % (self.PMIN, self.PAVG/self.PCOUNT, self.PMAX))
      print("")

    print("       Loading: %s" % self.secondsToTime(self.TimeDuration("Load")))
    print("       Parsing: %s" % self.secondsToTime(self.TimeDuration("Parse")))
    if self.gotTimeDuration("Compare"):
      print("     Comparing: %s" % self.secondsToTime(self.TimeDuration("Compare")))
    if self.gotTimeDuration("Rescan"):
      print("    Rescanning: %s" % self.secondsToTime(self.TimeDuration("Rescan")))

    if len(self.THREADS_HIST) != 0:
      print("   Downloading: %s" % self.secondsToTime(self.TimeDuration("Download")))

    print(" TOTAL RUNTIME: %s" % self.secondsToTime(self.TimeDuration("Total")))

  def secondsToTime(self, seconds, withMillis=True):
    ms = int(100 * (seconds - int(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)

    if d == 0:
      t = "%02d:%02d:%02d" % (h, m, s)
    else:
      t = "%dd %02d:%02d:%02d" % (d, h, m, s)

    if withMillis: t = "%s.%02d" % (t, ms)

    return t

#
# Simple container for media items under consideration for processing
#
# status is 0 (not to be loaded) or 1 (to be loaded).
#
# missingOK is used to specify that if an image cannot be loaded,
# don't complain (ie. speculative loading, eg. season-all.tbn)
#
class MyMediaItem(object):
  def __init__(self, mediaType, imageType, name, season, episode, filename, dbid, cachedurl, libraryid, missingOK):
    self.status = 1 # 0=OK, 0=Ignore
    self.mtype = mediaType
    self.itype = imageType
    self.name = name
    self.season = season
    self.episode = episode
    self.filename = filename
    self.dbid = dbid
    self.cachedurl = cachedurl
    self.libraryid = libraryid
    self.missingOK = missingOK

  def __str__(self):
    return "{%d, %s, %s, %s, %s, %s, %s, %d, %s, %s, %s}" % \
            (self.status, self.mtype, self.itype, self.name, self.season, \
             self.episode, self.filename,  self.dbid, self.cachedurl, \
             self.libraryid, self.missingOK)

  def getFullName(self):
    if self.episode:
      return "%s, %s Episode %s" % (self.name, self.season, self.episode)
    elif self.season:
      if self.itype == "cast.thumb":
        return "Cast Member %s in %s" % (self.name, self.season)
      elif self.mtype == "tvshows":
        return "%s, %s" % (self.name, self.season)
      else:
        return "%s by %s" % (self.name, " & ".join(self.season))
    else:
      return "%s" % (self.name)

def removeNonAscii(s, replaceWith = ""):
  if replaceWith == "":
    return  "".join([x if ord(x) < 128 else ("%%%02x" % ord(x)) for x in s])
  else:
    return  "".join([x if ord(x) < 128 else replaceWith for x in s])

#
# Load data using JSON-RPC. In the case of TV Shows, also load Seasons
# and Episodes into a single data structure.
#
# Sets doesn't support filters, so filter this list after retrieval.
#
def jsonQuery(action, mediatype, filter="", force=False, extraFields=False, rescan=False, \
                      decode=False, nodownload=False, lastRun=False, labels=None, query=""):

  if not mediatype in ["addons", "albums", "artists", "songs", "movies", "sets", "tags", "tvshows", "pvr.tv", "pvr.radio"]:
    gLogger.out("Error: %s is not a valid media class" % mediatype, newLine=True)
    sys.exit(2)

  # Only QA movies and tvshows (and sub-types) for now...
  if action == "qa" and rescan and not mediatype in ["movies", "tags", "sets", "tvshows", "seasons", "episodes"]:
    gLogger.out("Error: media class [%s] is not currently supported by qax" % mediatype, newLine=True)
    sys.exit(2)

  # Only songa, movies and tvshows (and sub-types) valid for missing...
  if action == "missing" and not mediatype in ["songs", "movies", "tvshows", "seasons", "episodes"]:
    gLogger.out("Error: media class [%s] is not currently supported by missing" % mediatype, newLine=True)
    sys.exit(2)

  TOTALS.TimeStart(mediatype, "Total")

  jcomms = MyJSONComms(gConfig, gLogger)
  database = MyDB(gConfig, gLogger)

  if mediatype == "tvshows": TOTALS.addSeasonAll()

  gLogger.progress("Loading %s..." % mediatype, every = 1)

  TOTALS.TimeStart(mediatype, "Load")

  if action == "query":
    secondaryFields = parseQuery(query)[0]
  else:
    secondaryFields = None

  if mediatype in ["pvr.tv", "pvr.radio"] and not gConfig.HAS_PVR:
    (section_name, title_name, id_name, data) = ("", "", "", [])
  else:
    (section_name, title_name, id_name, data) = jcomms.getData(action, mediatype, filter, extraFields, lastRun=lastRun, secondaryFields=secondaryFields)

  if data and "result" in data and section_name in data["result"]:
    data = data["result"][section_name]
  else:
    data = []

  if data and filter and mediatype in ["addons", "sets", "pvr.tv", "pvr.radio"]:
    gLogger.log("Filtering %s on %s = %s" % (mediatype, title_name, filter))
    filteredData = []
    for d in data:
      if re.search(filter, d[title_name], re.IGNORECASE):
        filteredData.append(d)
    data = filteredData

  # Combine PVR channelgroups with PVR channels to create a hierarchical structure that can be parsed
  if mediatype in ["pvr.tv", "pvr.radio"]:
    pvrdata = []
    for cg in data:
      (s1, t1, i1, data1) = jcomms.getData(action, "%s.channel" % mediatype, filter, extraFields, channelgroupid=cg["channelgroupid"], lastRun=lastRun, secondaryFields=secondaryFields)
      if "result" in data1:
        channels = []
        for channel in data1["result"].get(s1, None):
          if "label" in channel: del channel["label"]
          channels.append(channel)
        pvrdata.append({"label":          cg["label"],
                        "channeltype":    cg["channeltype"],
                        "channelgroupid": cg["channelgroupid"],
                        "channels": channels})
    data = pvrdata

  if mediatype == "tvshows":
    for tvshow in data:
      title = tvshow["title"]
      gLogger.progress("Loading TV Show: [%s]..." % title, every = 1)
      (s2, t2, i2, data2) = jcomms.getData(action, "seasons", filter, extraFields, showid=tvshow[id_name], lastRun=lastRun)
      limits = data2["result"]["limits"]
      if limits["total"] == 0: continue
      tvshow[s2] = data2["result"][s2]
      for season in tvshow[s2]:
        seasonid = season["season"]
        gLogger.progress("Loading TV Show: [%s, Season %d]..." % (title, seasonid), every = 1)
        (s3, t3, i3, data3) = jcomms.getData(action, "episodes", filter, extraFields, showid=tvshow[id_name], seasonid=season[i2], lastRun=lastRun, secondaryFields=secondaryFields)
        limits = data3["result"]["limits"]
        if limits["total"] == 0: continue
        season[s3] = data3["result"][s3]

  if lastRun and mediatype in ["movies", "tvshows"]:
    # Create a new list containing only tvshows with episodes...
    if mediatype == "tvshows":
      newData = []
      for tvshow in data:
        newtvshow = {}
        epCount = 0
        for season in tvshow.get("seasons", {}):
          if season.get("episodes", None):
            if newtvshow == {}:
              newtvshow = tvshow
              del newtvshow["seasons"]
              newtvshow["seasons"] = []
            newtvshow["seasons"].append(season)
            epCount += len(season.get("episodes", {}))
        if newtvshow != {}:
          newData.append(newtvshow)
          gLogger.out("Recently added TV Show: %s (%d episode%s)" % (tvshow.get("title"), epCount, "s"[epCount==1:]), newLine=True)
      data = newData
    else:
      for item in data:
        gLogger.out("Recently added Movie: %s" % item.get("title", item.get("artist", item.get("name", None))), newLine=True)

    if len(data) != 0: gLogger.out("", newLine=True)

  TOTALS.TimeEnd(mediatype, "Load")

  if data != []:
    if action == "cache":
      cacheImages(mediatype, jcomms, database, data, title_name, id_name, force, nodownload)
    elif action == "qa":
      qaData(mediatype, jcomms, database, data, title_name, id_name, rescan)
    elif action == "dump":
      jcomms.dumpJSON(data, decode)
    elif action == "missing":
      fileList = jcomms.getAllFilesForSource(mediatype, labels)
      missingFiles(mediatype, data, fileList, title_name, id_name)
    elif action == "query":
      queryLibrary(mediatype, query, data, title_name, id_name)

  gLogger.progress("")

  TOTALS.TimeEnd(mediatype, "Total")

#
# Parse the supplied JSON data, turning it into a list of artwork urls
# (mediaitems) that should be matched against the database (cached files)
# to determine which should be skipped (those in the cache, unless
# force update is true).
#
# Those that are not skipped will be added to a queueu for processing by
# 1..n threads. Errors will be added to an error queue by the threads, and
# subsueqently displayed to the user at the end.
#
def cacheImages(mediatype, jcomms, database, data, title_name, id_name, force, nodownload):

  mediaitems = []
  imagecache = {}

  TOTALS.TimeStart(mediatype, "Parse")

  parseURLData(jcomms, mediatype, mediaitems, imagecache, data, title_name, id_name)

  TOTALS.TimeEnd(mediatype, "Parse")

  # Don't need this data anymore, make it available for garbage collection
  del data
  del imagecache

  TOTALS.TimeStart(mediatype, "Compare")

  gLogger.progress("Loading database items...")
  dbfiles = {}
  with database:
    rows = database.getAllColumns().fetchall()
    for r in rows: dbfiles[r[3]] = r

  gLogger.log("Loaded %d items from texture cache database" % len(dbfiles))

  gLogger.progress("Matching database items...")

  ITEMLIMIT = -1 if nodownload else 100

  itemCount = 0
  for item in mediaitems:
    if item.mtype == "tvshows" and item.season == "Season All": TOTALS.bump("Season-all", item.itype)

    filename = urllib2.unquote(re.sub("^image://(.*)/","\\1",item.filename))
    if sys.version_info >= (3, 0):
      filename = bytes(filename, "utf-8").decode("iso-8859-1")

    dbrow = dbfiles.get(filename, None)

    if not dbrow:
      filename = item.filename[:-1]
      dbrow = dbfiles.get(filename, None)

    # Don't need to cache file if it's already in the cache, unless forced...
    # Assign the texture cache database id and cachedurl so that removal will be quicker.
    if dbrow:
      if force:
        itemCount += 1
        item.status = 1
        item.dbid = dbrow[0]
        item.cachedurl = dbrow[1]
      else:
        if gLogger.VERBOSE and gLogger.LOGGING: gLogger.log("ITEM SKIPPED: %s" % item)
        TOTALS.bump("Skipped", item.itype)
        item.status = 0
    # These items we are missing from the cache...
    else:
      itemCount += 1
      item.status = 1
      if not force:
        if ITEMLIMIT == -1 or itemCount < ITEMLIMIT:
          MSG = "Need to cache: [%-10s] for %s: %s\n" % (item.itype.center(10), re.sub("(.*)s$", "\\1", item.mtype), item.getFullName())
          gLogger.out(MSG)
        elif itemCount == ITEMLIMIT:
          gLogger.out("...and many more! (First %d items shown)\n" % ITEMLIMIT)

  TOTALS.TimeEnd(mediatype, "Compare")

  # Don't need this data anymore, make it available for garbage collection
  del dbfiles

  if nodownload:
    TOTALS.addNotCached()
    for item in mediaitems:
      if item.status == 1: TOTALS.bump("Not in Cache", item.itype)

  gLogger.progress("")

  if itemCount > 0 and not nodownload:
    single_work_queue = Queue.Queue()
    multiple_work_queue = Queue.Queue()
    error_queue = Queue.Queue()

    gLogger.out("\n")

    # Identify unique itypes, so we can group items in the queue
    # This is crucial to working out when the first/last item is loaded
    # in order to calculate accurate elapsed times by itype
    unique_items = {}
    for item in mediaitems:
      if not item.itype in unique_items:
        unique_items[item.itype] = True

    c = sc = mc = 0
    for ui in sorted(unique_items):
      for item in mediaitems:
        if item.status == 1 and item.itype == ui:
          c += 1

          isSingle = False
          if gConfig.SINGLETHREAD_URLS:
            for site in gConfig.SINGLETHREAD_URLS:
              if site.search(item.filename):
                sc += 1
                if gLogger.VERBOSE and gLogger.LOGGING: gLogger.log("QUEUE ITEM: single [%s], %s" % (site.pattern, item))
                single_work_queue.put(item)
                item.status = 0
                isSingle = True
                break

          if not isSingle:
            mc += 1
            if gLogger.VERBOSE and gLogger.LOGGING: gLogger.log("QUEUE ITEM: %s" % item)
            multiple_work_queue.put(item)
            item.status = 0

          gLogger.progress("Queueing work item: Single thread %d, Multi thread %d" % (sc, mc), every=50, finalItem=(c==itemCount))

    # Don't need this data anymore, make it available for garbage collection
    del mediaitems

    TOTALS.TimeStart(mediatype, "Download")

    THREADS = []

    if not single_work_queue.empty():
      gLogger.log("Creating 1 thread for single access sites")
      t = MyImageLoader(True, single_work_queue, multiple_work_queue, error_queue, itemCount, gConfig, gLogger, TOTALS, force, 10)
      THREADS.append(t)
      t.setDaemon(True)
      t.start()

    if not multiple_work_queue.empty():
      tCount = gConfig.DOWNLOAD_THREADS["download.threads.%s" % mediatype]
      THREADCOUNT = tCount if tCount <= mc else mc
      gLogger.log("Creating %d image download threads" % THREADCOUNT)
      for i in range(THREADCOUNT):
        t = MyImageLoader(False, multiple_work_queue, single_work_queue, error_queue, itemCount, gConfig, gLogger, TOTALS, force, 10)
        THREADS.append(t)
        t.setDaemon(True)
        t.start()

    try:
      ALIVE = True
      while ALIVE:
        ALIVE = False
        for t in THREADS: ALIVE = True if t.isAlive() else ALIVE
        if ALIVE: time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
      stopped.set()
      gLogger.progress("Please wait while threads terminate...")
      ALIVE = True
      while ALIVE:
        ALIVE = False
        for t in THREADS: ALIVE = True if t.isAlive() else ALIVE
        if ALIVE: time.sleep(0.1)

    TOTALS.TimeEnd(mediatype, "Download")

    gLogger.progress("", newLine=True, noBlank=True)

    if not error_queue.empty():
      gLogger.out("\nThe following items could not be downloaded:\n\n")
      while not error_queue.empty():
        item = error_queue.get()
        name = item.getFullName()[:40]
        gLogger.out("[%-10s] [%-40s] %s\n" % (item.itype, name, urllib2.unquote(item.filename)))
        gLogger.log("ERROR ITEM: %s" % item)
        error_queue.task_done()

#
# Iterate over all the elements, seeking out artwork to be stored in a list.
# Use recursion to process season and episode sub-elements.
#
def parseURLData(jcomms, mediatype, mediaitems, imagecache, data, title_name, id_name, showName = None, season = None, pvrGroup = None):
  gLogger.reset()

  SEASON_ALL = (showName != None and season == None)

  for item in data:
    if title_name in item: title = item[title_name]

    if showName:
      mediatype = "tvshows"
      name = showName
      if season:
        episode = re.sub("([0-9]*x[0-9]*)\..*", "\\1", title)
        name = "%s, %s Episode %s" % (showName, season, episode)
      else:
        episode = None
        name = "%s, %s" % (showName, title)
    elif pvrGroup:
        name = "%s, %s" % (pvrGroup, title)
        episode = None
    else:
      name = title
      if title_name != "artist" and "artist" in item:
        season = item["artist"]
      else:
        season = None
      episode = None

    gLogger.progress("Parsing [%s]..." % name, every = 25)

    for a in ["fanart", "poster", "thumb", "thumbnail"]:
      if a in item and evaluateURL(a, item[a], imagecache):
        mediaitems.append(MyMediaItem(mediatype, a, name, season, episode, item[a], 0, None, item[id_name], False))

    if "art" in item:
      if SEASON_ALL and "poster" in item["art"]:
        SEASON_ALL = False
        (poster_url, fanart_url, banner_url) = jcomms.getSeasonAll(item["art"]["poster"])
        if poster_url and evaluateURL("poster", poster_url, imagecache):
          mediaitems.append(MyMediaItem(mediatype, "poster", name, "Season All", None, poster_url, 0, None, item[id_name], False))
        if fanart_url and evaluateURL("fanart", fanart_url, imagecache):
          mediaitems.append(MyMediaItem(mediatype, "fanart", name, "Season All", None, fanart_url, 0, None, item[id_name], False))
        if banner_url and evaluateURL("banner", banner_url, imagecache):
          mediaitems.append(MyMediaItem(mediatype, "banner", name, "Season All", None, banner_url, 0, None, item[id_name], False))

      for a in item["art"]:
        imgtype_short = a.replace("tvshow.","")
        if evaluateURL(imgtype_short, item["art"][a], imagecache):
          mediaitems.append(MyMediaItem(mediatype, imgtype_short, name, season, episode, item["art"][a], 0, None, item[id_name], False))

    if "cast" in item:
      for a in item["cast"]:
        if "thumbnail" in a and evaluateURL("cast.thumb", a["thumbnail"], imagecache):
          mediaitems.append(MyMediaItem(mediatype, "cast.thumb", a["name"], name, None, a["thumbnail"], 0, None, item[id_name], False))

    if "seasons" in item:
      parseURLData(jcomms, "seasons", mediaitems, imagecache, item["seasons"], "label", "season", showName=title)
    if "episodes" in item:
      parseURLData(jcomms, "episodes", mediaitems, imagecache, item["episodes"], "label", "episodeid", showName=showName, season=title)
      season = None
    if "channels" in item:
      parseURLData(jcomms, "%s.channel" % mediatype, mediaitems, imagecache, item["channels"], "channel", "channelid", pvrGroup=title)

# Include or exclude url depending on basic properties - has it
# been "seen" before (in which case, discard as no point caching
# it twice. Or discard if matches an "ignore" rule.
#
# Otherwise include it, and add it to the "seen" cache so it can
# be excluded in future if seen again.
#
def evaluateURL(imgtype, url, imagecache):
  if not url or url == "": return False

  if url in imagecache:
    TOTALS.bump("Duplicate", imgtype)
    imagecache[url] += 1
    return False

  if gConfig.CACHE_IGNORE_TYPES:
    for ignore in gConfig.CACHE_IGNORE_TYPES:
      if ignore.search(url):
        gLogger.log("Ignored image due to rule [%s]: %s" % (ignore.pattern, url))
        TOTALS.bump("Ignored", imgtype)
        imagecache[url] = 1
        return False

  imagecache[url] = 0
  return True

def qaData(mediatype, jcomms, database, data, title_name, id_name, rescan, work=None, mitems=None, showName=None, season=None, pvrGroup=None):
  gLogger.reset()

  if mitems == None:
      TOTALS.TimeStart(mediatype, "Parse")
      workItems= {}
      mediaitems = []
  else:
      workItems = work
      mediaitems = mitems

  zero_items = []
  blank_items = []
  art_items = []
  check_file = False

  check_file = (gConfig.QA_FILE and mediatype in ["movies", "tags", "episodes"])

  zero_items.extend(gConfig.getQAFields("zero", mediatype, stripModifier=False))
  blank_items.extend(gConfig.getQAFields("blank", mediatype, stripModifier=False))
  art_items.extend(gConfig.getQAFields("art", mediatype, stripModifier=False))

  for item in data:
    libraryid = item[id_name]

    if title_name in item: title = item[title_name]

    if showName:
      if season:
        episode = re.sub("([0-9]*x[0-9]*)\..*", "\\1", title)
        name = "%s, %s Episode %s" % (showName, season, episode)
      else:
        episode = None
        name = "%s, %s" % (showName, title)
    elif pvrGroup:
        name = "%s, %s" % (pvrGroup, title)
    else:
      name = title
      season = None
      episode = None

    gLogger.progress("Parsing [%s]..." % name, every = 25)

    missing = {}

    for i in zero_items:
      j = i[1:] if i.startswith("?") else i
      if not j in item or item[j] == 0: missing["Zero %s" % j] = not i.startswith("?")

    for i in blank_items:
      j = i[1:] if i.startswith("?") else i
      if not j in item or item[j] == "" or item[j] == [] or item[j] == [""]: missing["Missing %s" % j] = not i.startswith("?")

    for i in art_items:
      j = i[1:] if i.startswith("?") else i
      if "art" in item:
        artwork = item.get("art", {}).get(j, "")
      else:
        artwork = item.get(j, "")
      if artwork == "":
        missing["Missing %s" % j] = not i.startswith("?")
      else:
        FAILED = False
        if gConfig.QA_FAIL_TYPES:
          for qafailtype in gConfig.QA_FAIL_TYPES:
            if qafailtype.search(artwork):
              missing["Fail URL (%s, \"%s\")" % (j, qafailtype.pattern)] = True
              FAILED = True
              break
        if not FAILED and gConfig.QA_WARN_TYPES:
          for qawarntype in gConfig.QA_WARN_TYPES:
            if qawarntype.search(artwork):
              missing["Warn URL (%s, \"%s\")" % (j, qawarntype.pattern)] = False
              break
#      elif database.getRowByFilename(artwork) == None: missing["Uncached %s" % j] = False

    if check_file and not ("file" in item and jcomms.getFileDetails(item["file"])): missing["file"] = False

    if "seasons" in item:
      qaData("seasons", jcomms, database, item["seasons"], "label", "season", False, \
              work=workItems, mitems=mediaitems, showName=title)
    if "episodes" in item:
      qaData("episodes", jcomms, database, item["episodes"], "label", "episodeid", False, \
              work=workItems, mitems=mediaitems, showName=showName, season=title)
      season = None
    if "channels" in item:
      qaData("%s.channel" % mediatype, jcomms, database, item["channels"], "channel", "channelid", False, \
              work=workItems, mitems=mediaitems, pvrGroup=title)

    if missing != {}:
      if len(name) > 50: name = "%s...%s" % (name[0:23], name[-24:])
      if mediatype.startswith("pvr."):
        mtype = mediatype
      else:
        mtype = mediatype[:-1].capitalize()
        if mtype == "Tvshow": mtype = "TVShow"
      mediaitems.append("%s [%-50s]: %s" % (mtype, name[0:50], ", ".join(missing)))
      if "file" in item and "".join(["Y" if missing[m] else "" for m in missing]) != "":
        dir = "%s;%s" % (mediatype, os.path.dirname(item["file"]))
        libraryids = workItems[dir] if dir in workItems else []
        libraryids.append(libraryid)
        workItems[dir] = libraryids
#      else:
#        gLogger.out("ERROR: No file for QA item - won't rescan [%s]" % name, newLine=True)

  if mitems == None:
    TOTALS.TimeEnd(mediatype, "Parse")
    gLogger.progress("")
    for m in mediaitems: gLogger.out("%s\n" % m)

  if rescan:
    TOTALS.TimeStart(mediatype, "Rescan")
    jcomms.rescanDirectories(workItems)
    TOTALS.TimeEnd(mediatype, "Rescan")

def missingFiles(mediatype, data, fileList, title_name, id_name, showName=None, season=None):
  gLogger.reset()

  if showName == None:
      TOTALS.TimeStart(mediatype, "Parse")

  for item in data:
    libraryid = item[id_name]

    if title_name in item: title = item[title_name]

    if showName:
      name = showName
      if season:
        episode = re.sub("([0-9]*x[0-9]*)\..*", "\\1", title)
        name = "%s, %s Episode %s" % (showName, season, episode)
      else:
        season = title
        episode = None
        name = "%s, %s" % (showName, season)
    else:
      name = title
      season = None
      episode = None

    gLogger.progress("Parsing [%s]..." % name, every = 25)

    # Remove matched file from fileList - what files remain at the end
    # will be reported to the user
    if "file" in item and mediatype != "tvshows":
      file = "%s" % item["file"]
      try:
        fileList.remove(file)
      except ValueError:
        pass

    if "seasons" in item:
      missingFiles("seasons", item["seasons"], fileList, "label", "season", showName=title)
    if "episodes" in item:
      missingFiles("episodes", item["episodes"], fileList, "label", "episodeid", showName=showName, season=title)
      season = None

  if showName == None:
    TOTALS.TimeEnd(mediatype, "Parse")
    gLogger.progress("")
    if fileList != []:
      gLogger.out("The following media files are not present in the \"%s\" media library:\n\n" % mediatype)
      for file in fileList: gLogger.out("%s\n" % file)

# Extract data, using optional simple search, or complex SQL filter.
def sqlExtract(ACTION="NONE", search="", filter=""):
  database = MyDB(gConfig, gLogger)

  with database:
    SQL = ""
    if (search != "" or filter != ""):
      if search != "": SQL = "WHERE t.url LIKE '%" + search + "%' ORDER BY t.id ASC"
      if filter != "": SQL = filter + " "

    IDS=""
    FSIZE=0
    FCOUNT=0

    database.getAllColumns(SQL)

    while True:
      row = database.fetchone()
      if row == None: break

      IDS = "%s %s" % (IDS, str(row[0]))
      FCOUNT += 1

      if ACTION == "NONE":
        database.dumpRow(row)
      elif ACTION == "EXISTS":
        if not os.path.exists(gConfig.getFilePath(row[1])):
          database.dumpRow(row)
      elif ACTION == "STATS":
        if os.path.exists(gConfig.getFilePath(row[1])):
          FSIZE += os.path.getsize(gConfig.getFilePath(row[1]))
          database.dumpRow(row)

    if ACTION == "STATS":
      gLogger.out("\nFile Summary: %s files; Total size: %s Kbytes\n\n" % (format(FCOUNT, ",d"), format(int(FSIZE/1024), ",d")))

    if (search != "" or filter != ""): gLogger.progress("Matching row ids:%s\n" % IDS)

def queryLibrary(mediatype, query, data, title_name, id_name, work=None, mitems=None, showName=None, season=None, pvrGroup=None):
  gLogger.reset()

  if mitems == None:
      TOTALS.TimeStart(mediatype, "Parse")
      workItems= {}
      mediaitems = []
  else:
      workItems = work
      mediaitems = mitems

  fields, tuples = parseQuery(query)

  for item in data:
    libraryid = item[id_name]

    if id_name == "songid":
      if title_name in item and "artist" in item:
        title = "%s (%s)" % (item[title_name], "/".join(item["artist"]))
    else:
      if title_name in item: title = item[title_name]

    if showName:
      if season:
        episode = re.sub("([0-9]*x[0-9]*)\..*", "\\1", title)
        name = "%s, %s Episode %s" % (showName, season, episode)
      else:
        episode = None
        name = "%s, %s" % (showName, title)
    elif pvrGroup:
        name = "%s, %s" % (pvrGroup, title)
    else:
      name = title
      season = None
      episode = None

    gLogger.progress("Parsing [%s]..." % name, every = 25)

    RESULTS = []

    try:
      for field, field_split, condition, inverted, value, logic in tuples:
        temp = item
        for f in field_split:
          temp = searchItem(temp, f)
          if temp == None: break

        if temp != None:
          if type(temp) is list:
            for t in temp:
              MATCHED = evaluateCondition(t, condition, value)
              if inverted: MATCHED = not MATCHED
              if MATCHED: break
            matched_value = ", ".join(temp)
          else:
            if temp is str and temp.startswith("image://"): temp = urllib2.unquote(temp)
            MATCHED = evaluateCondition(temp, condition, value)
            if inverted: MATCHED = not MATCHED
            matched_value = temp
        else:
          MATCHED = False
          matched_value = None

        RESULTS.append([MATCHED, logic, field, matched_value])
    except:
      pass

    MATCHED = False
    FIELDS = []
    DISPLAY = ""
    for matched, logic, field, value in RESULTS:
      if logic == "and":
        if matched == False: MATCHED = False
      elif logic == "or":
        if matched == True: MATCHED = True
      elif logic == None:
        MATCHED = matched
      else:
        MATCHED = False

      # Only output each field value once...
      if not field in FIELDS:
        FIELDS.append(field)
        try:
          throw_exception = value + 1
          DISPLAY = "%s, %s = %s" % (DISPLAY, field, value)
        except:
          DISPLAY = "%s, %s = \"%s\"" % (DISPLAY, field, value)

    if MATCHED: mediaitems.append([name, DISPLAY[2:]])

    if "seasons" in item:
      queryLibrary("seasons", query, item["seasons"], "label", "season", \
              work=workItems, mitems=mediaitems, showName=title)
    if "episodes" in item:
      queryLibrary("episodes", query, item["episodes"], "label", "episodeid", \
              work=workItems, mitems=mediaitems, showName=showName, season=title)
      season = None
    if "channels" in item:
      queryLibrary("%s.channel" % mediatype, query, item["channels"], "channel", "channelid", \
              work=workItems, mitems=mediaitems, pvrGroup=title)

  if mitems == None:
    TOTALS.TimeEnd(mediatype, "Parse")
    gLogger.progress("")
    for m in mediaitems:
      gLogger.out("Matched: [%-50s] %s" % (m[0], m[1]), newLine=True)

def searchItem(data, field):
  if field in data: return data[field]

  if type(data) is list:
    tList = []
    for item in data:
      value = searchItem(item, field)
      if value: tList.append(value)
    return tList

  return None

def evaluateCondition(input, condition, value):
  if type(input) is int: value = int(value)
  if type(input) is float: value = float(value)

  if condition in ["=", "=="]:    return (input == value)
  elif condition == "!=":         return (input != value)
  elif condition == ">":          return (input > value)
  elif condition == "<":          return (input < value)
  elif condition == ">=":         return (input >= value)
  elif condition == "<=":         return (input <= value)
  elif condition == "contains":   return (input.find(value) != -1)
  elif condition == "startswith": return (input.startswith(value))
  elif condition == "endswith":   return (input.endswith(value))

  return False

def parseQuery(query):
  condition = ["==", "=", "!=", ">", ">=", "<", "<=", "contains", "startswith", "endswith"]
  logic = ["and", "or"]

  fields = []
  tuples = []

  FIELDNAME_NEXT = True
  tField = tValue = tCondition = tLogic = None

  newValue = ""
  IN_STR=False
  for value in query:
    if value == "'" or value == '"': IN_STR = not IN_STR
    if value == " " and IN_STR:
      newValue = "%s\t" % newValue
    else:
      newValue = "%s%s" % (newValue, value)

  INVERT=False
  for value in newValue.split(" "):
    if value == "": continue
    value_lower = value.lower()

    if value_lower == "not":
      INVERT=True
      continue

    #and, or etc.
    if value_lower  in logic:
      FIELDNAME_NEXT=True
      tLogic = value
    # ==, >=, contains etc.
    elif value_lower in condition:
      if value == "=": value = "=="
      FIELDNAME_NEXT=False
      tCondition = value
    #Value
    elif not FIELDNAME_NEXT:
      if value.startswith("'") or value.startswith('"'): value = value[1:]
      if value.endswith("'") or value.endswith('"'): value = value[:-1]
      FIELDNAME_NEXT=True
      tValue = value.replace("\t", " ")
      tuples.append([tField, tField.split("."), tCondition, INVERT, tValue, tLogic])
      INVERT=False
    #Field name
    else:
      tField = value_lower
      fields.append(tField.split(".")[0])
      FIELDNAME_NEXT=False

  return ",".join(fields), tuples

# Delete row by id, and corresponding file item
def sqlDelete( ids=[] ):
  database = MyDB(gConfig, gLogger)
  with database:
    for id in ids:
      try:
        database.deleteItem(int(id))
      except ValueError:
        gLogger.out("id %s is not valid\n" % id)
        continue

def dirScan(removeOrphans=False, purge_nonlibrary_artwork=False, libraryFiles=None, keyIsHash=False):
  database = MyDB(gConfig, gLogger)

  with database:
    dbfiles = {}
    ddsmap = {}
    orphanedfiles = []
    localfiles = []

    re_search_addon = re.compile("^.*%s.xbmc%saddons%s.*" % (os.sep, os.sep, os.sep))
    re_search_mirror = re.compile("^http://mirrors.xbmc.org/addons/.*")

    gLogger.progress("Loading texture cache...")

    rows = database.getAllColumns().fetchall()
    for r in rows: dbfiles[r[1]] = r
    for r in rows: ddsmap[r[1][:-4]] = r[1]
    gLogger.log("Loaded %d rows from texture cache" % len(rows))

    gLogger.progress("Scanning Thumbnails directory...")

    path = gConfig.getFilePath()

    for (root, dirs, files) in os.walk(path):
      newroot = root.replace(path,"")
      basedir = os.path.basename(newroot)
      for file in files:
        if basedir == "":
          hash = file
        else:
          hash = "%s/%s" % (basedir, file)

        gLogger.progress("Scanning Thumbnails directory [%s]..." % hash, every=25)

        # If its a dds file, it should be associated with another
        # file with the same hash, but different extension. Find
        # this other file in the ddsmap - if its there, ignore
        # the dds file, otherwise leave the dds file to be reported
        # as an orphaned file.
        if hash[-4:] == ".dds":
          ddsfile = hash[:-4]
          if ddsmap.get(ddsfile, None): continue

        row = dbfiles.get(hash, None)
        if not row:
          filename = os.path.join(newroot, file)
          gLogger.log("Orphan file detected: [%s] with likely hash [%s]" % (filename, hash))
          orphanedfiles.append(filename)
        elif libraryFiles:
          URL = removeNonAscii(row[3])

          isRetained = False
          if gConfig.PRUNE_RETAIN_TYPES:
            for retain in gConfig.PRUNE_RETAIN_TYPES:
              if retain.search(URL):
                gLogger.log("Retained image due to rule [%s]" % retain.pattern)
                isRetained = True
                break

          # Ignore add-on/mirror related images
          if not re_search_addon.search(URL) and \
             not re_search_mirror.search(URL) and \
             not isRetained:

            key = hash[:-4] if keyIsHash else URL

            if not key in libraryFiles:
              # Last ditch attempt to find a matching key, database might be
              # slightly mangled
              if not keyIsHash:
                key = getKeyFromFilename(row[3])
                if key in libraryFiles:
                  del libraryFiles[key]
                else:
                  localfiles.append(row)
              else:
                localfiles.append(row)
            else:
               del libraryFiles[key]

    gLogger.progress("")

    # Orphan check, with optional remove...
    if libraryFiles == None:
      gLogger.log("Identified %d orphaned files" % len(orphanedfiles))
      if removeOrphans and gConfig.ORPHAN_LIMIT_CHECK and len(orphanedfiles) > (len(dbfiles)/20):
        gLogger.log("Something is wrong here, that's far too many orphaned files - 5% limit exceeded!")
        gLogger.out("Found %d orphaned files for %d database files.\n"  % (len(orphanedfiles), len(dbfiles)))
        gLogger.out("This is far too many orphaned files for this number of database files, something may be wrong.\n")
        gLogger.out("Check your configuration, database, and Thumbnails folder.\n\n")
        gLogger.out("Add \"orphan.limit.check = no\" to the properties file if you want to disable this check.\n")
        return
      for ofile in orphanedfiles:
        gLogger.out("Orphaned file found: Name [%s], Created [%s], Size [%d]%s\n" % \
          (ofile,
           time.ctime(os.path.getctime(gConfig.getFilePath(ofile))),
           os.path.getsize(gConfig.getFilePath(ofile)),
           ", REMOVING..." if removeOrphans else ""))
        if removeOrphans:
          gLogger.log("Removing orphan file: %s" % gConfig.getFilePath(ofile))
          os.remove(gConfig.getFilePath(ofile))

    # Prune, with optional remove...
    if libraryFiles != None:
      if localfiles != []:
        if purge_nonlibrary_artwork:
          gLogger.out("Pruning cached images from texture cache...", newLine=True)
        else:
          gLogger.out("The following items are present in the texture cache but not the media library:", newLine=True)
        gLogger.out("", newLine=True)
      FSIZE=0
      for row in localfiles:
        database.dumpRow(row)
        FSIZE += os.path.getsize(gConfig.getFilePath(row[1]))
        if purge_nonlibrary_artwork: database.deleteItem(row[0], row[1])
      gLogger.out("\nSummary: %s files; Total size: %s Kbytes\n\n" % (format(len(localfiles),",d"), format(int(FSIZE/1024), ",d")))

def getHash(string):
  string = string.lower()
  bytes = bytearray(string)
  crc = 0xffffffff;
  for b in bytes:
    crc = crc ^ (b << 24)
    for i in range(8):
      if (crc & 0x80000000): crc = (crc << 1) ^ 0x04C11DB7
      else: crc = crc << 1;
    crc = crc & 0xFFFFFFFF
  return '%08x' % crc

# The following method is extremely slow on a Raspberry Pi, and
# doesn't work well with unicode strings (returns wrong hash).
# Fortunately, using the encoded url/filename as the key (next
# function) is sufficient for our needs and also about twice
# as fast on a Pi.
def getKeyFromHash(filename):
  url = re.sub("^image://(.*)/","\\1",filename)
  url = urllib2.unquote(url)
  hash = getHash(url)
  return "%s%s%s" % (hash[0:1], os.sep, hash)

def getKeyFromFilename(filename):
  f = urllib2.unquote(re.sub("^image://(.*)/","\\1",filename))

  if sys.version_info >= (3, 0):
    f = bytes(f, "utf-8").decode("iso-8859-1")

  return removeNonAscii(f)

def getAllFiles(keyFunction):

  jcomms = MyJSONComms(gConfig, gLogger)

  files = {}

  REQUEST = [
              {"method":"AudioLibrary.GetAlbums",
                "params":{"sort": {"order": "ascending", "method": "label"},
                          "properties":["title", "fanart", "thumbnail"]}},

              {"method":"AudioLibrary.GetArtists",
               "params":{"sort": {"order": "ascending", "method": "artist"},
                         "properties":["fanart", "thumbnail"], "albumartistsonly": False}},

              {"method":"AudioLibrary.GetSongs",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "properties":["title", "fanart", "thumbnail"]}},

              {"method":"AudioLibrary.GetGenres",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "properties":["title", "thumbnail"]}},

              {"method":"VideoLibrary.GetMusicVideos",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "properties":["title", "thumbnail", "fanart", "art"]}},

              {"method":"VideoLibrary.GetMovies",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "properties":["title", "cast", "art"]}},

              {"method":"VideoLibrary.GetMovieSets",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "properties":["title", "art"]}},

              {"method":"VideoLibrary.GetGenres",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "type": "movie",
                         "properties":["title", "thumbnail"]}},

              {"method":"VideoLibrary.GetGenres",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "type": "tvshow",
                         "properties":["title", "thumbnail"]}},

              {"method":"VideoLibrary.GetGenres",
               "params":{"sort": {"order": "ascending", "method": "title"},
                         "type": "musicvideo",
                         "properties":["title", "thumbnail"]}},

              {"method":"Addons.GetAddons",
               "params":{"properties":["name", "thumbnail", "fanart"]}}
             ]

  for r in REQUEST:
    mediatype = re.sub(".*\.Get(.*)","\\1",r["method"])

    gLogger.progress("Loading: %s..." % mediatype)
    data = jcomms.sendJSON(r, "libFiles")

    for items in data["result"]:
      if items != "limits":
        if mediatype in ["MovieSets","Addons","Genres"]:
          interval = 0
        else:
          interval = int(int(data["result"]["limits"]["total"])/10)
          interval = 50 if interval > 50 else interval
        title = ""
        for i in data["result"][items]:
          title = i.get("title", i.get("artist", i.get("name", None)))
          gLogger.progress("Parsing: %s [%s]..." % (mediatype, title), every=interval)
          if "fanart" in i: files[keyFunction(i["fanart"])] = "fanart"
          if "thumbnail" in i: files[keyFunction(i["thumbnail"])] = "thumbnail"
          if "art" in i:
            for a in i["art"]:
              files[keyFunction(i["art"][a])] = a
          if "cast" in i:
            for c in i["cast"]:
              if "thumbnail" in c:
                files[keyFunction(c["thumbnail"])] = "cast.thumb"
        if title != "": gLogger.progress("Parsing: %s [%s]..." % (mediatype, title))

  gLogger.progress("Loading: TVShows...")

  REQUEST = {"method":"VideoLibrary.GetTVShows",
             "params": {"sort": {"order": "ascending", "method": "title"},
                        "properties":["title", "cast", "art"]}}

  tvdata = jcomms.sendJSON(REQUEST, "libTV")

  if "result" in tvdata and "tvshows" in tvdata["result"]:
    for tvshow in tvdata["result"]["tvshows"]:
      gLogger.progress("Parsing: TVShows [%s]..." % tvshow["title"])
      tvshowid = tvshow["tvshowid"]
      for a in tvshow["art"]:
        files[keyFunction(tvshow["art"][a])] = a
      if "cast" in tvshow:
        for c in tvshow["cast"]:
          if "thumbnail" in c:
            files[keyFunction(c["thumbnail"])] = "cast.thumb"

      REQUEST = {"method":"VideoLibrary.GetSeasons",
                 "params":{"tvshowid": tvshowid,
                           "sort": {"order": "ascending", "method": "season"},
                           "properties":["season", "art"]}}

      seasondata = jcomms.sendJSON(REQUEST, "libTV")

      if "seasons" in seasondata["result"]:
        SEASON_ALL = True
        for season in seasondata["result"]["seasons"]:
          seasonid = season["season"]
          gLogger.progress("Parsing: TVShows [%s, Season %d]..." % (tvshow["title"], seasonid))
          for a in season["art"]:
            if SEASON_ALL and a in ["poster", "tvshow.poster", "tvshow.fanart", "tvshow.banner"]:
              SEASON_ALL = False
              (poster_url, fanart_url, banner_url) = jcomms.getSeasonAll(season["art"][a])
              if poster_url: files[keyFunction(poster_url)] = "poster"
              if fanart_url: files[keyFunction(fanart_url)] = "fanart"
              if banner_url: files[keyFunction(banner_url)] = "banner"
            files[keyFunction(season["art"][a])] = a

          REQUEST = {"method":"VideoLibrary.GetEpisodes",
                     "params":{"tvshowid": tvshowid, "season": seasonid,
                               "properties":["cast", "art"]}}

          episodedata = jcomms.sendJSON(REQUEST, "libTV")

          for episode in episodedata["result"]["episodes"]:
            episodeid = episode["episodeid"]
            for a in episode["art"]:
              files[keyFunction(episode["art"][a])] = a
            if "cast" in episode:
              for c in episode["cast"]:
                if "thumbnail" in c:
                  files[keyFunction(c["thumbnail"])] = "cast.thumb"

  # PVR Channels
  if gConfig.HAS_PVR:
    gLogger.progress("Loading: PVR Channels...")
    for channelType in ["tv", "radio"]:
      REQUEST = {"method":"PVR.GetChannelGroups",
                 "params":{"channeltype": channelType}}
      pvrdata = jcomms.sendJSON(REQUEST, "libPVR", checkResult=False)
      if "result" in pvrdata:
        for channelgroup in pvrdata["result"].get("channelgroups", None):
          REQUEST = {"method":"PVR.GetChannels",
                     "params":{"channelgroupid": channelgroup["channelgroupid"],
                               "properties": ["channeltype", "channel", "thumbnail"]}}
          channeldata = jcomms.sendJSON(REQUEST, "libPVR", checkResult=False)
          if "result" in channeldata:
            for channel in channeldata["result"].get("channels", None):
              files[keyFunction(channel["thumbnail"])] = "pvr.thumb"

  return files

def pruneCache( purge_nonlibrary_artwork=False ):

  files = getAllFiles(keyFunction=getKeyFromFilename)

  dirScan("N", purge_nonlibrary_artwork, libraryFiles=files, keyIsHash=False)

def doLibraryScan(media, path):
  jcomms = MyJSONComms(gConfig, gLogger)

  scanMethod = "VideoLibrary.Scan" if media == "video" else "AudioLibrary.Scan"

  jcomms.scanDirectory(scanMethod, path)

  if media == "video":
    return jcomms.vUpdateCount
  else:
    return jcomms.aUpdateCount

def doLibraryClean(media):
  jcomms = MyJSONComms(gConfig, gLogger)

  cleanMethod = "VideoLibrary.Clean" if media == "video" else "AudioLibrary.Clean"

  jcomms.cleanLibrary(cleanMethod)

def getDirectoryList(path, mediatype = "files"):
  jcomms = MyJSONComms(gConfig, gLogger)

  data = jcomms.getDirectoryList(mediatype, path)

  if not "result" in data:
    print("No directory listing available.")
    return

  files = data["result"]["files"]
  for file in files:
    ftype = file["filetype"]
    fname = file["file"]

    if ftype == "directory":
      FTYPE = "DIR"
      FNAME = os.path.dirname(fname)
    else:
      FTYPE = "FILE"
      FNAME = fname

    print("%s: %s") % (FTYPE, FNAME)

def showSources(media=None, withLabel=None):
  jcomms = MyJSONComms(gConfig, gLogger)

  mlist = [media] if media else ["video", "music", "pictures", "files", "programs"]

  for m in mlist:
    for s in jcomms.getSources(m, labelPrefix=True, withLabel=withLabel):
      gLogger.out("%s: %s" % (m, s), newLine=True)

def setPower(state):
  if state in ["hibernate", "reboot", "shutdown", "suspend"]:
    MyJSONComms(gConfig, gLogger).setPower(state)
  else:
    gLogger.out("Invalid power state: %s" % state, newLine=True)

def execAddon(addon, params, wait=False):
  REQUEST = {"method":"Addons.ExecuteAddon",
             "params": {"addonid": addon, "wait": wait}}

  if params: REQUEST["params"]["params"] = params

  MyJSONComms(gConfig, gLogger).sendJSON(REQUEST, "libAddon")

def showStatus(idleTime=600):
  jcomms = MyJSONComms(gConfig, gLogger)

  STATUS = []

  REQUEST = {"method": "XBMC.GetInfoBooleans",
             "params": { "booleans": ["System.ScreenSaverActive", "Library.IsScanningMusic", "Library.IsScanningVideo"] }}
  data = jcomms.sendJSON(REQUEST, "libSSaver")
  if "result" in data:
    STATUS.append("Scanning Music: %s" % ("Yes" if data["result"].get("Library.IsScanningMusic",False) else "No"))
    STATUS.append("Scanning Video: %s" % ("Yes" if data["result"].get("Library.IsScanningVideo",False) else "No"))
    STATUS.append("ScreenSaver Active: %s" % ("Yes" if data["result"].get("System.ScreenSaverActive",False) else "No"))

  property = "System.IdleTime(%s) " % idleTime
  REQUEST = {"method": "XBMC.GetInfoBooleans", "params": { "booleans": [property] }}
  data = jcomms.sendJSON(REQUEST, "libIdleTime")
  if "result" in data:
    STATUS.append("System Idle > %ss: %s" % (idleTime, ("Yes" if data["result"].get(property,False) else "No")))

  STATUS.append("PVR Enabled: %s" % ("Yes" if gConfig.HAS_PVR else "No"))

  REQUEST = {"method":"Player.GetActivePlayers"}
  data = jcomms.sendJSON(REQUEST, "libGetPlayers")
  if "result" in data:
    for player in data["result"]:
      if "playerid" in player:
        pType = player["type"]
        pId = player["playerid"]
        STATUS.append("Player: %s" % pType.capitalize())

        REQUEST = {"method": "Player.GetItem", "params": {"playerid": pId}}
        data = jcomms.sendJSON(REQUEST, "libGetItem")

        if "result" in data and "item" in data["result"]:
          item = data["result"]["item"]
          iType = item.get("type", None)
          libraryId = item.get("id", None)

          if libraryId == None and "label" in item:
            title = item["label"]
          elif iType == "song":
            title = jcomms.getSongName(libraryId)
          elif iType == "movie":
            title = jcomms.getMovieName(libraryId)
          elif iType == "episode":
            title = jcomms.getEpisodeName(libraryId)
          else:
            title = None

          STATUS.append("Activity: %s" % iType.capitalize())
          STATUS.append("Title: %s" % title)

    if data["result"] == []:
      STATUS.append("Player: None")

  if STATUS != []:
    for x in STATUS:
      pos = x.find(":")
      gLogger.out("%-20s: %s" % (x[:pos], x[pos+2:]), newLine=True)

def showNotifications():
  MyJSONComms(gConfig, gLogger).listen()

def usage(EXIT_CODE):
  print("Version: %s" % gConfig.VERSION)
  print("")
  print("Usage: " + os.path.basename(__file__) + " sS <string> | xXf [sql-filter] | dD <id[id id]>] |" \
        "rR | c [class [filter]] | nc [class [filter]] | | lc [class] | lnc [class] | C class filter | jJ class [filter] | qa class [filter] | qax class [filter] | pP | missing class src-label [src-label]* | ascan [path] |vscan [path] | aclean | vclean | sources [media] | sources media [label] | directory path | config | version | update | status [idleTime] | monitor | power <state> | exec [params] | execw [params]")
  print("")
  print("  s       Search url column for partial movie or tvshow title. Case-insensitive.")
  print("  S       Same as \"s\" (search) but will validate cachedurl file exists, displaying only those that fail validation")
  print("  x       Extract details, using optional SQL filter")
  print("  X       Same as \"x\" (extract) but will validate cachedurl file exists, displaying only those that fail validation")
  print("  f       Same as x, but include file summary (file count, accumulated file size)")
  print("  d       Delete rows with matching ids, along with associated cached images")
  print("  r       Reverse search to identify \"orphaned\" Thumbnail files not present in texture cache")
  print("  R       Same as \"r\" (reverse search) but automatically deletes \"orphaned\" Thumbnail files")
  print("  c       Re-cache missing artwork. Class can be movies, tags, sets, tvshows, artists, albums or songs.")
  print("  C       Re-cache artwork even when it exists. Class can be movies, tags, sets, tvshows, artists, albums or songs. Filter mandatory.")
  print("  nc      Same as c, but don't actually cache anything (ie. see what is missing). Class can be movies, tags, sets, tvshows, artists, albums or songs.")
  print("  lc      Like c, but only for content added since the modification date of the file specficied in property lastrunfile")
  print("  lnc     Like nc, but only for content added since the modification date of the file specficied in property lastrunfile")
  print("  j       Query library by class (movies, tags, sets, tvshows, artists, albums or songs) with optional filter, return JSON results.")
  print("  J       Same as \"j\", but includes extra JSON audio/video fields as defined in properties file.")
  print("  jd, Jd  Functionality equivalent to j/J, but all urls are decoded")
  print("  qa      Run QA check on movies, tags and tvshows, identifying media with missing artwork or plots")
  print("  qax     Same as qa, but remove and rescan those media items with missing details.")
  print("          Configure with qa.zero.*, qa.blank.* and qa.art.* properties. Prefix field with ? to render warning only.")
  print("  p       Display files present in texture cache that don't exist in the media library")
  print("  P       Prune (automatically remove) cached items that don't exist in the media library")
  print("  missing Locate media files missing from the specified media library, matched against one or more source labels, eg. missing movies \"My Movies\"")
  print("  ascan   Scan entire audio library, or specific path")
  print("  vscan   Scan entire video library, or specific path")
  print("  aclean  Clean audio library")
  print("  vclean  Clean video library")
  print("  sources List all sources, or sources for specfic media type (video, music, pictures, files, programs) or label (eg. \"My Movies\")")
  print("directory Retrieve list of files in a specific directory (see sources)")
  print("  status  Display state of client - ScreenSaverActive, SystemIdle (default 600 seconds), active Player state etc.")
  print("  monitor Display client event notifications as they occur")
  print("  power   Control power state of client, where state is one of suspend, hibernate, shutdown and reboot")
  print("  exec    Execute specified addon, with optional parameters")
  print("  execw   Execute specified addon, with optional parameters and wait (although often wait has no effect)")
  print("")
  print("  config  Show current configuration")
  print("  version Show current version and check for new version")
  print("  update  Update to new version (if available)")
  print("")
  print("Valid media classes: addons, pvr.tv, pvr.radio, artists, albums, songs, movies, sets, tags, tvshows")
  print("Valid meta classes:  music (artists + albums + songs) and video (movies + sets + tvshows) and all (music + video + addons + pvr.tv + pvr.radio)")
  print("Meta classes can be used in place of media classes for: c/C/nc/lc/lnc/j/J/jd/Jd/qa/qax options.")
  print("")
  print("SQL Filter fields:")
  print("  id, cachedurl, height, width, usecount, lastusetime, lasthashcheck, url")

  sys.exit(EXIT_CODE)

def loadConfig(argv):
  global DBVERSION, MYWEB, MYSOCKET, MYDB
  global TOTALS
  global gConfig, gLogger

  DBVERSION = MYWEB = MYSOCKET = MYDB = None

  gConfig = MyConfiguration(argv)
  gLogger = MyLogger()
  TOTALS  = MyTotals(gConfig.LASTRUNFILE_DATETIME)

  gLogger.DEBUG = gConfig.DEBUG
  gLogger.VERBOSE = gConfig.LOGVERBOSE
  gLogger.setLogFile(gConfig.LOGFILE)

  gLogger.log("Command line args: %s" % sys.argv)
  gLogger.log("Current version #: v%s" % gConfig.VERSION)
  gLogger.log("Current platform : %s" % sys.platform)
  gLogger.log("Python  version #: v%d.%d.%d.%d (%s)" % (sys.version_info[0], sys.version_info[1], \
                                               sys.version_info[2], sys.version_info[4], sys.version_info[3]))

def checkConfig(option):

  jsonNeedVersion = 6

  if option in ["c","C"]:
    needWeb = True
  else:
    needWeb = False

  if option in ["c","C","nc","lc","lnc","j","jd","J","Jd","qa","qax","query", "p","P",
                "vscan", "ascan", "vclean", "aclean", "directory", "sources",
                "status", "monitor", "power",
                "exec", "execw", "missing"]:
    needSocket = True
  else:
    needSocket = False

  if option in ["s","S","x","X","f","c","C","nc","lc","lnc","qa","qax","d","r","R","p","P"]:
    needDb = True
  else:
    needDb = False

  gotWeb = gotSocket = gotDb = False
  jsonGotVersion = 0

  if needWeb:
    try:
      defaultTimeout = socket.getdefaulttimeout()
      socket.setdefaulttimeout(7.5)
      jcomms = MyJSONComms(gConfig, gLogger)
      REQUEST = {}
      REQUEST["jsonrpc"] = "2.0"
      REQUEST["method"] = "JSONRPC.Ping"
      REQUEST["id"] =  "libPing"

      data = json.loads(jcomms.sendWeb("POST", "/jsonrpc", json.dumps(REQUEST), timeout=5))
      if "result" in data and data["result"] == "pong":
        gotWeb = True

      socket.setdefaulttimeout(defaultTimeout)
    except socket.error:
      pass

  if needWeb and not gotWeb:
    MSG = "FATAL: The task you wish to perform requires that the web server is\n" \
          "       enabled and running on the XBMC system you wish to connect.\n\n" \
          "       A connection cannot be established to the following webserver:\n" \
          "       %s:%s\n\n" \
          "       Check settings in properties file %s\n" % (gConfig.XBMC_HOST, gConfig.WEB_PORT, gConfig.CONFIG_NAME)
    gLogger.out(MSG)
    return False

  if needSocket:
    try:
      jcomms = MyJSONComms(gConfig, gLogger)
      REQUEST = {"method": "JSONRPC.Ping"}
      data = jcomms.sendJSON(REQUEST, "libPing", timeout=7.5, checkResult=False)
      if "result" in data and data["result"] == "pong":
        gotSocket = True

      REQUEST = {"method": "JSONRPC.Version"}
      data = jcomms.sendJSON(REQUEST, "libVersion", timeout=7.5, checkResult=False)
      if "result" in data:
        if "version" in data["result"]:
          jsonGotVersion = data["result"]["version"]
          if type(jsonGotVersion) is dict and "major" in jsonGotVersion:
            jsonGotVersion = jsonGotVersion["major"]

      REQUEST = {"method": "XBMC.GetInfoBooleans",
                 "params": { "booleans": ["System.GetBool(pvrmanager.enabled)"] }}
      data = jcomms.sendJSON(REQUEST, "libPVR", checkResult=False)
      gConfig.HAS_PVR = ("result" in data and data["result"].get("System.GetBool(pvrmanager.enabled)", False))
    except socket.error:
      pass

  if needSocket and not gotSocket:
    MSG = "FATAL: The task you wish to perform requires that the JSON-RPC server is\n" \
          "       enabled and running on the XBMC system you wish to connect.\n\n" \
          "       A connection cannot be established to the following JSON-RPC server:\n" \
          "       %s:%s\n\n" \
          "       Check settings in properties file %s\n" % (gConfig.XBMC_HOST, gConfig.RPC_PORT, gConfig.CONFIG_NAME)
    gLogger.out(MSG)
    return False

  if needSocket and jsonGotVersion  < jsonNeedVersion :
    MSG = "FATAL: The task you wish to perform requires that a JSON-RPC server with\n" \
          "       version %d or above of the XBMC JSON-RPC API is provided.\n\n" \
          "       The JSON-RPC API version of the connected server is: %d (0 means unknown)\n\n" \
          "       Check settings in properties file %s\n" % (jsonNeedVersion, jsonGotVersion, gConfig.CONFIG_NAME)
    gLogger.out(MSG)
    return False

  if needDb:
    try:
      database = MyDB(gConfig, gLogger)
      con = database.getDB()
      if database.DBVERSION < 13:
        MSG = "WARNING: The sqlite3 database pre-dates Frodo (v12), some problems may be encountered!\n"
        gLogger.out(MSG)
        gLogger.log(MSG)
      gotDb = True
    except lite.OperationalError:
      pass

  if needDb and not gotDb:
    MSG = "FATAL: The task you wish to perform requires read/write file\n" \
          "       access to the XBMC sqlite3 Texture Cache database.\n\n" \
          "       The following sqlite3 database could not be opened:\n" \
          "       %s\n\n" \
          "       Check settings in properties file %s\n" % (gConfig.getDBPath(), gConfig.CONFIG_NAME)
    gLogger.out(MSG)
    return False

  return True

def checkUpdate(forcedCheck = False):
  (remoteVersion, remoteHash) = getLatestVersion()

  if forcedCheck:
    print("Latest  Version: %s" % "v" + remoteVersion if remoteVersion else "Unknown")
    print("")

  if remoteVersion and remoteVersion > gConfig.VERSION:
    print("A new version of this script is available - use the \"update\" option to automatically apply update.")
    print("")

  if forcedCheck:
    url = gConfig.GITHUB.replace("//raw.","//").replace("/master","/blob/master")
    print("Full changelog: %s/CHANGELOG.md" % url)

def getLatestVersion():
  try:
    response = urllib2.urlopen("%s/%s" % (gConfig.GITHUB, "VERSION"))
    data = response.read()
    return data.replace("\n","").split(" ")
  except:
    return (None, None)

def downloadLatestVersion(force=False):
  (remoteVersion, remoteHash) = getLatestVersion()

  if not remoteVersion:
    print("FATAL: Unable to determine version of the latest file, check internet is available.")
    sys.exit(2)

  if not force and remoteVersion <= gConfig.VERSION:
    print("Current version is already up to date - no update required.")
    sys.exit(2)

  try:
    response = urllib2.urlopen("%s/%s" % (gConfig.GITHUB, "texturecache.py"))
    data = response.read()
  except:
    print("FATAL: Unable to download latest file, check internet is available.")
    sys.exit(2)

  digest = hashlib.md5()
  digest.update(data)

  if (digest.hexdigest() != remoteHash):
    print("FATAL: Hash of new download is not correct, possibly corrupt, abandoning update.")
    sys.exit(2)

  path = os.path.realpath(__file__)
  dir = os.path.dirname(path)

  if os.path.exists("%s%s.git" % (dir, os.sep)):
    print("FATAL: Might be updating version in git repository... Abandoning update!")
    sys.exit(2)

  try:
    THISFILE = open(os.path.realpath(__file__), "wb")
    THISFILE.write(data)
    THISFILE.close()
  except:
    print("FATAL: Unable to update current file, check you have write access")
    sys.exit(2)

  print("Successfully updated to version v%s" % remoteVersion)

def main(argv):

  loadConfig(argv)

  if len(argv) == 0: usage(1)

  if not checkConfig(argv[0]): sys.exit(2)

  EXIT_CODE = 0

  multi_call_m = ["albums", "artists", "songs"]
  multi_call_v = ["movies", "sets", "tvshows"]
  multi_call   = ["addons", "pvr.tv", "pvr.radio"] + multi_call_m + multi_call_v

  if gConfig.CHECKUPDATE and not argv[0] in ["version","update"]: checkUpdate()

  if argv[0] == "s" and len(argv) == 2:
    sqlExtract("NONE", argv[1], "")
  elif argv[0] == "S" and len(argv) == 2:
    sqlExtract("EXISTS", argv[1], "")

  elif argv[0] == "x" and len(argv) == 1:
    sqlExtract("NONE")
  elif argv[0] == "x" and len(argv) == 2:
    sqlExtract("NONE", "", argv[1])
  elif argv[0] == "X" and len(argv) == 1:
    sqlExtract("EXISTS")

  elif argv[0] == "f" and len(argv) == 1:
    sqlExtract("STATS")
  elif argv[0] == "f" and len(argv) == 2:
    sqlExtract("STATS", "", argv[1])

  elif argv[0] in ["c", "C", "nc", "lc", "lnc", "j", "J", "jd", "Jd", "qa", "qax", "query"]:

    if argv[0] in ["j", "J", "jd", "Jd"]:
      _action = "dump"
      _stats  = False
    elif argv[0] in ["qa", "qax"]:
      _action = "qa"
      _stats  = False
    elif argv[0] in ["query"]:
      _action = "query"
      _stats  = False
    else:
      _action = "cache"
      _stats  = True

    _force      = True if argv[0] == "C" else False
    _rescan     = True if argv[0] == "qax" else False
    _lastRun    = True if argv[0] in ["lc", "lnc"] else False
    _nodownload = True if argv[0] in ["nc", "lnc"] else False
    _decode     = True if argv[0] in ["jd", "Jd"] else False
    _extraFields= True if argv[0] in ["J", "Jd"] else False

    _filter     = ""
    _query      = ""

    if argv[0] != "query":
      _filter     = argv[2] if len(argv) > 2 else ""
    else:
      if len(argv) == 3:
        _query      = argv[2] if len(argv) > 2 else ""
      else:
        _filter     = argv[2]
        _query      = argv[3]

    if _force and not gConfig.RECACHEALL and _filter == "":
      print("Forcing re-cache of all items is disabled. Enable by setting \"allow.recacheall=yes\" in property file.")
      sys.exit(2)

    _multi_call = []
    if len(argv) == 1:
      _multi_call = multi_call
      _multi_call.remove("songs")
    else:
      if argv[1] == "video": _multi_call = multi_call_v
      if argv[1] == "music": _multi_call = multi_call_m
      if argv[1] == "all":   _multi_call = multi_call

    if _multi_call != []:
      for x in _multi_call:
        jsonQuery(_action, mediatype=x, filter=_filter,
                  force=_force, lastRun=_lastRun, nodownload=_nodownload,
                  rescan=_rescan, decode=_decode, extraFields=_extraFields)
      if _stats: TOTALS.libraryStats(multi=_multi_call, filter=_filter, lastRun=_lastRun, query=_query)
    elif len(argv) >= 2:
      jsonQuery(_action, mediatype=argv[1], filter=_filter,
                force=_force, lastRun=_lastRun, nodownload=_nodownload,
                rescan=_rescan, decode=_decode, extraFields=_extraFields, query=_query)
      if _stats: TOTALS.libraryStats(item=argv[1], filter=_filter, lastRun=_lastRun, query=_query)
    else:
      usage(1)

  elif argv[0] == "d" and len(argv) >= 2:
    sqlDelete(argv[1:])

  elif argv[0] == "r":
    dirScan(removeOrphans=False)

  elif argv[0] == "R":
    dirScan(removeOrphans=True)

  elif argv[0] == "p" and len(argv) == 1:
    pruneCache(purge_nonlibrary_artwork=False)

  elif argv[0] == "P" and len(argv) == 1:
    pruneCache(purge_nonlibrary_artwork=True)

  elif argv[0] == "vscan":
    EXIT_CODE = doLibraryScan("video", path = argv[1] if len(argv) == 2 else None)

  elif argv[0] == "ascan":
    EXIT_CODE = doLibraryScan("audio", path = argv[1] if len(argv) == 2 else None)

  elif argv[0] == "vclean":
    doLibraryClean("video")

  elif argv[0] == "aclean":
    doLibraryClean("audio")

  elif argv[0] == "directory" and len(argv) == 2:
    getDirectoryList(argv[1])

  elif argv[0] == "sources" and len(argv) < 3:
    showSources(media = argv[1] if len(argv) == 2 else None)
  elif argv[0] == "sources" and len(argv) == 3:
    showSources(media = argv[1], withLabel = argv[2])

  elif argv[0] == "status":
    if len(argv) == 2:
      showStatus(idleTime = argv[1])
    else:
      showStatus()

  elif argv[0] == "monitor":
    showNotifications()

  elif argv[0] == "version":
    print("Current Version: v%s" % gConfig.VERSION)
    checkUpdate(forcedCheck = True)

  elif argv[0] == "config":
    gConfig.showConfig()

  elif argv[0] == "update":
    downloadLatestVersion()
  elif argv[0] == "fupdate":
    downloadLatestVersion(force=True)

  elif argv[0] == "power" and len(argv) == 2:
    setPower(argv[1])

  elif argv[0] == "exec" and len(argv) > 1:
    execAddon(argv[1], argv[2:], wait=False)
  elif argv[0] == "execw" and len(argv) > 1:
    execAddon(argv[1], argv[2:], wait=True)

  elif argv[0] == "missing" and len(argv) >= 3:
    jsonQuery(action="missing", mediatype=argv[1], labels=argv[2:])

  else:
    usage(1)

  sys.exit(EXIT_CODE)

if __name__ == "__main__":
  try:
    stopped = threading.Event()
    main(sys.argv[1:])
  except (KeyboardInterrupt, SystemExit) as e:
    if type(e) == SystemExit: sys.exit(int(str(e)))
