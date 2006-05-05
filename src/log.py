###
# Copyright (c) 2002-2005, Jeremiah Fincher
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import os
import sys
import time
import types
import atexit
import logging
import operator
import textwrap
import traceback

import supybot.ansi as ansi
import supybot.conf as conf
import supybot.utils as utils
import supybot.registry as registry

import supybot.ircutils as ircutils

deadlyExceptions = [KeyboardInterrupt, SystemExit]

###
# This is for testing, of course.  Mostly is just disables the firewall code
# so exceptions can propagate.
###
testing = False

class Formatter(logging.Formatter):
    _fmtConf = staticmethod(lambda : conf.supybot.log.format())
    def formatTime(self, record, datefmt=None):
        return timestamp(record.created)

    def formatException(self, (E, e, tb)):
        for exn in deadlyExceptions:
            if issubclass(e.__class__, exn):
                raise
        return logging.Formatter.formatException(self, (E, e, tb))

    def format(self, record):
        self._fmt = self._fmtConf()
        return logging.Formatter.format(self, record)


class PluginFormatter(Formatter):
    _fmtConf = staticmethod(lambda : conf.supybot.log.plugins.format())


class Logger(logging.Logger):
    def exception(self, *args):
        (E, e, tb) = sys.exc_info()
        tbinfo = traceback.extract_tb(tb)
        path = '[%s]' % '|'.join(map(operator.itemgetter(2), tbinfo))
        eStrId = '%s:%s' % (E, path)
        eId = hex(hash(eStrId) & 0xFFFFF)
        logging.Logger.exception(self, *args)
        self.error('Exception id: %s', eId)
        # The traceback should be sufficient if we want it.
        # self.error('Exception string: %s', eStrId)

    def _log(self, level, msg, args, exc_info=None):
        msg = format(msg, *args)
        logging.Logger._log(self, level, msg, (), exc_info=exc_info)


class StdoutStreamHandler(logging.StreamHandler):
    def format(self, record):
        s = logging.StreamHandler.format(self, record)
        if record.levelname != 'ERROR' and conf.supybot.log.stdout.wrap():
            # We check for ERROR there because otherwise, tracebacks (which are
            # already wrapped by Python itself) wrap oddly.
            if not isinstance(record.levelname, basestring):
                print record
                print record.levelname
                print utils.stackTrace()
            prefixLen = len(record.levelname) + 1 # ' '
            s = textwrap.fill(s, width=78, subsequent_indent=' '*prefixLen)
            s.rstrip('\r\n')
        return s

    def emit(self, record):
        if conf.supybot.log.stdout() and not conf.daemonized:
            try:
                logging.StreamHandler.emit(self, record)
            except ValueError, e: # Raised if sys.stdout is closed.
                self.disable()
                error('Error logging to stdout.  Removing stdout handler.')
                exception('Uncaught exception in StdoutStreamHandler:')

    def disable(self):
        self.setLevel(sys.maxint) # Just in case.
        _logger.removeHandler(self)
        logging._acquireLock()
        try:
            del logging._handlers[self]
        finally:
            logging._releaseLock()


class BetterFileHandler(logging.FileHandler):
    def emit(self, record):
        msg = self.format(record)
        if not hasattr(types, "UnicodeType"): #if no unicode support...
            self.stream.write(msg)
            self.stream.write(os.linesep)
        else:
            try:
                self.stream.write(msg)
                self.stream.write(os.linesep)
            except UnicodeError:
                self.stream.write(msg.encode("utf8"))
                self.stream.write(os.linesep)
        self.flush()


class ColorizedFormatter(Formatter):
    # This was necessary because these variables aren't defined until later.
    # The staticmethod is necessary because they get treated like methods.
    _fmtConf = staticmethod(lambda : conf.supybot.log.stdout.format())
    def formatException(self, (E, e, tb)):
        if conf.supybot.log.stdout.colorized():
            return ''.join([ansi.RED,
                            Formatter.formatException(self, (E, e, tb)),
                            ansi.RESET])
        else:
            return Formatter.formatException(self, (E, e, tb))

    def format(self, record, *args, **kwargs):
        if conf.supybot.log.stdout.colorized():
            color = ''
            if record.levelno == logging.CRITICAL:
                color = ansi.WHITE + ansi.BOLD
            elif record.levelno == logging.ERROR:
                color = ansi.RED
            elif record.levelno == logging.WARNING:
                color = ansi.YELLOW
            if color:
                return ''.join([color,
                                Formatter.format(self, record, *args, **kwargs),
                                ansi.RESET])
            else:
                return Formatter.format(self, record, *args, **kwargs)
        else:
            return Formatter.format(self, record, *args, **kwargs)

# These are public.
formatter = Formatter('NEVER SEEN; IF YOU SEE THIS, FILE A BUG!')
pluginFormatter = PluginFormatter('NEVER SEEN; IF YOU SEE THIS, FILE A BUG!')

# These are not.
logging.setLoggerClass(Logger)
_logger = logging.getLogger('supybot')

class ValidLogLevel(registry.String):
    """Invalid log level."""
    minimumLevel = -1
    def set(self, s):
        s = s.upper()
        try:
            level = logging._levelNames[s]
        except KeyError:
            try:
                level = int(s)
            except ValueError:
                self.error()
        if level < self.minimumLevel:
            self.error()
        self.setValue(level)

    def __str__(self):
        # The str() is necessary here; apparently getLevelName returns an
        # integer on occasion.  logging--
        level = str(logging.getLevelName(self.value))
        if level.startswith('Level'):
            level = level.split()[-1]
        return level

class LogLevel(ValidLogLevel):
    """Invalid log level.  Value must be either DEBUG, INFO, WARNING,
    ERROR, or CRITICAL."""
    def setValue(self, v):
        ValidLogLevel.setValue(self, v)
        _logger.setLevel(self.value) # _logger defined later.

conf.registerGlobalValue(conf.supybot.directories, 'log',
    conf.Directory('logs', """Determines what directory the bot will store its
    logfiles in."""))

conf.registerGroup(conf.supybot, 'log')
conf.registerGlobalValue(conf.supybot.log, 'format',
    registry.String('%(levelname)s %(asctime)s %(name)s %(message)s',
    """Determines what the bot's logging format will be.  The relevant
    documentation on the available formattings is Python's documentation on
    its logging module."""))
conf.registerGlobalValue(conf.supybot.log, 'level',
    LogLevel(logging.INFO, """Determines what the minimum priority level logged
    will be.  Valid values are DEBUG, INFO, WARNING, ERROR,
    and CRITICAL, in order of increasing priority."""))
conf.registerGlobalValue(conf.supybot.log, 'timestampFormat',
    registry.String('%Y-%m-%dT%H:%M:%S', """Determines the format string for
    timestamps in logfiles.  Refer to the Python documentation for the time
    module to see what formats are accepted. If you set this variable to the
    empty string, times will be logged in a simple seconds-since-epoch
    format."""))

class BooleanRequiredFalseOnWindows(registry.Boolean):
    def set(self, s):
        registry.Boolean.set(self, s)
        if self.value and os.name == 'nt':
            raise registry.InvalidRegistryValue, \
                  'Value cannot be true on Windows.'

conf.registerGlobalValue(conf.supybot.log, 'stdout',
    registry.Boolean(True, """Determines whether the bot will log to
    stdout."""))
conf.registerGlobalValue(conf.supybot.log.stdout, 'colorized',
    BooleanRequiredFalseOnWindows(False, """Determines whether the bot's logs
    to stdout (if enabled) will be colorized with ANSI color."""))
conf.registerGlobalValue(conf.supybot.log.stdout, 'wrap',
    registry.Boolean(True, """Determines whether the bot will wrap its logs
    when they're output to stdout."""))
conf.registerGlobalValue(conf.supybot.log.stdout, 'format',
    registry.String('%(levelname)s %(asctime)s %(message)s',
    """Determines what the bot's logging format will be.  The relevant
    documentation on the available formattings is Python's documentation on
    its logging module."""))

conf.registerGroup(conf.supybot.log, 'plugins')
conf.registerGlobalValue(conf.supybot.log.plugins, 'individualLogfiles',
    registry.Boolean(False, """Determines whether the bot will separate plugin
    logs into their own individual logfiles."""))
conf.registerGlobalValue(conf.supybot.log.plugins, 'format',
    registry.String('%(levelname)s %(asctime)s %(message)s',
    """Determines what the bot's logging format will be.  The relevant
    documentation on the available formattings is Python's documentation on
    its logging module."""))


# These just make things easier.
debug = _logger.debug
info = _logger.info
warning = _logger.warning
error = _logger.error
critical = _logger.critical
exception = _logger.exception

# These were just begging to be replaced.
registry.error = error
registry.exception = exception

setLevel = _logger.setLevel

atexit.register(logging.shutdown)

# ircutils will work without this, but it's useful.
ircutils.debug = debug

def getPluginLogger(name):
    if not conf.supybot.log.plugins.individualLogfiles():
        return _logger
    log = logging.getLogger('supybot.plugins.%s' % name)
    if not log.handlers:
        filename = os.path.join(pluginLogDir, '%s.log' % name)
        handler = BetterFileHandler(filename)
        handler.setLevel(-1)
        handler.setFormatter(pluginFormatter)
        log.addHandler(handler)
    if name in sys.modules:
        log.info('Starting log for %s.', name)
    return log

def timestamp(when=None):
    if when is None:
        when = time.time()
    format = conf.supybot.log.timestampFormat()
    t = time.localtime(when)
    if format:
        return time.strftime(format, t)
    else:
        return str(int(time.mktime(t)))

def firewall(f, errorHandler=None):
    def logException(self, s=None):
        if s is None:
            s = 'Uncaught exception'
        if hasattr(self, 'log'):
            self.log.exception('%s:', s)
        else:
            exception('%s in %s.%s:', s, self.__class__.__name__, f.func_name)
    def m(self, *args, **kwargs):
        try:
            return f(self, *args, **kwargs)
        except Exception, e:
            if testing:
                raise
            logException(self)
            if errorHandler is not None:
                try:
                    errorHandler(self, *args, **kwargs)
                except Exception, e:
                    logException(self, 'Uncaught exception in errorHandler')

    m = utils.python.changeFunctionName(m, f.func_name, f.__doc__)
    return m

class MetaFirewall(type):
    def __new__(cls, name, bases, dict):
        if '__firewalled__' in dict:
            for attr in dict['__firewalled__']:
                try:
                    errorHandler = firewalled[attr]
                except: # This is raw here so people can still use tuples.
                    errorHandler = None
                dict[attr] = firewall(dict[attr], errorHandler)
        return super(MetaFirewall, cls).__new__(cls, name, bases, dict)
        #return type.__new__(cls, name, bases, dict)


_logDir = conf.supybot.directories.log()
if not os.path.exists(_logDir):
    os.mkdir(_logDir, 0755)

pluginLogDir = os.path.join(_logDir, 'plugins')

if not os.path.exists(pluginLogDir):
    os.mkdir(pluginLogDir, 0755)

try:
    messagesLogFilename = os.path.join(_logDir, 'messages.log')
    _handler = BetterFileHandler(messagesLogFilename)
except EnvironmentError, e:
    raise SystemExit, \
          'Error opening messages logfile (%s).  ' \
          'Generally, this is because you are running Supybot in a directory ' \
          'you don\'t have permissions to add files in, or you\'re running ' \
          'Supybot as a different user than you normal do.  The original ' \
          'error was: %s' % (messagesLogFilename, utils.gen.exnToString(e))
_handler.setFormatter(formatter)
_handler.setLevel(-1)
class PluginLogFilter(logging.Filter):
    def filter(self, record):
        if conf.supybot.log.plugins.individualLogfiles():
            if record.name.startswith('supybot.plugins'):
                return False
        return True
_handler.addFilter(PluginLogFilter())

_logger.addHandler(_handler)
_logger.setLevel(conf.supybot.log.level())

if not conf.daemonized:
    _stdoutHandler = StdoutStreamHandler(sys.stdout)
    _stdoutFormatter = ColorizedFormatter('IF YOU SEE THIS, FILE A BUG!')
    _stdoutHandler.setFormatter(_stdoutFormatter)
    _stdoutHandler.setLevel(-1)
    _logger.addHandler(_stdoutHandler)


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

