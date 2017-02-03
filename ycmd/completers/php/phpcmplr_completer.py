
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from builtins import *  # noqa
from future import standard_library
from future.utils import native
standard_library.install_aliases()

from ycmd.utils import ToBytes, ToUnicode, ProcessIsRunning
from ycmd.completers.completer import Completer
from ycmd import responses, utils

import json
import logging
import urllib.parse
import requests
import threading
import sys
import os
import traceback
import subprocess


KINDS_MAP = {
  'variable':        'v',
  'function':        'f',
  'const':           'd',
  'property':        'm',
  'static_property': 'm',
  'method':          'f',
  'static_method':   'f',
  'class_const':     'd',
  'class':           'c',
  'interface':       'c',
  'trait':           'c',
  'namespace':       'n',
}


class PhpCmplrCompleter( Completer ):

  def __init__( self, user_options ):
    super( PhpCmplrCompleter, self ).__init__( user_options )
    self._server_lock = threading.RLock()
    self._server_keep_logfiles = user_options[ 'server_keep_logfiles' ]
    self._php_path = 'php'
    self._phpcmplr_path = os.path.abspath(
                            os.path.join(
                              os.path.dirname( __file__ ),
                              '..',
                              '..',
                              '..',
                              'third_party',
                              'phpcmplr',
                              'bin',
                              'phpcmplr.php' ) )
    self._phpcmplr_host = '127.0.0.1'
    self._phpcmplr_port = None
    self._phpcmplr_url = None
    self._phpcmplr_phandle = None
    self._phpcmplr_stdout = None
    self._phpcmplr_stderr = None
    self._logger = logging.getLogger( __name__ )

    self._StartServer()


  def SupportedFiletypes( self ):
    return [ 'php' ]


  def Shutdown( self ):
    if self._ServerIsRunning():
      self._StopServer()


  def ServerIsHealthy( self ):
    if not self._ServerIsRunning():
      self._logger.debug( 'PhpCmplr not running.' )
      return False
    try:
      return self._GetResponse( '/ping' ) == {}
    except requests.exceptions.ConnectionError as e:
      return False


  def _ServerIsRunning( self ):
    with self._server_lock:
      return ( bool( self._phpcmplr_port ) and
               ProcessIsRunning( self._phpcmplr_phandle ) )


  def _RestartServer( self ):
    with self._server_lock:
      self._StopServer()
      self._StartServer()


  def _Reset( self ):
    self._phpcmplr_port = None
    self._phpcmplr_url = None
    self._phpcmplr_phandle = None
    self._phpcmplr_stdout = None
    self._phpcmplr_stderr = None

    if not self._server_keep_logfiles:
      if self._phpcmplr_stdout:
        utils.RemoveIfExists( self._phpcmplr_stdout )
      if self._phpcmplr_stderr:
        utils.RemoveIfExists( self._phpcmplr_stderr )


  def _StopServer( self ):
    with self._server_lock:
      self._logger.info( 'Stopping PhpCmplr' )
      if self._phpcmplr_phandle:
        self._logger.info( 'Stopping PhpCmplr with pid '
                           + str( self._phpcmplr_phandle.pid ) )
        self._GetResponse( '/quit' )
        self._phpcmplr_phandle.terminate()
        self._phpcmplr_phandle.wait()
        self._logger.info( 'PhpCmplr stopped' )

        self._Reset()


  def _StartServer( self ):
    with self._server_lock:
      if self._ServerIsRunning():
        return

      self._logger.info( 'Starting PhpCmplr' )
      self._phpcmplr_port = utils.GetUnusedLocalhostPort()
      self._phpcmplr_url = ToBytes( 'http://{0}:{1}'.format(
                                    self._phpcmplr_host,
                                    self._phpcmplr_port ) )

      command = [ self._php_path,
                  self._phpcmplr_path,
                  '--port', str( self._phpcmplr_port ) ]
      self._logger.debug( 'Starting PhpCmplr with command: {0}'.format( ' '.join( command ) ) )

      try:
        logfile_format = os.path.join( utils.PathToCreatedTempDir(),
                                       u'phpcmplr_{port}_{std}.log' )

        self._phpcmplr_stdout = logfile_format.format(
            port = self._phpcmplr_port,
            std = 'stdout' )

        self._phpcmplr_stderr = logfile_format.format(
            port = self._phpcmplr_port,
            std = 'stderr' )

        with utils.OpenForStdHandle( self._phpcmplr_stdout ) as stdout:
          with utils.OpenForStdHandle( self._phpcmplr_stderr ) as stderr:
            self._phpcmplr_phandle = utils.SafePopen( command,
                                                      stdin_windows = subprocess.PIPE,
                                                      stdout = stdout,
                                                      stderr = stderr )

      except Exception:
        self._logger.warning( 'Unable to start PhpCmplr server: '
                              + traceback.format_exc() )
        self._Reset()

      self._logger.info( 'PhpCmplr started with pid: ' +
                         str( self._phpcmplr_phandle.pid ) +
                         ' listening on port ' +
                         str( self._phpcmplr_port ) )


  def _GetResponse( self, handler, query = {}, request_data = {} ):
    """POST JSON data to PhpCmplr server and return JSON response."""
    handler = ToBytes( handler )
    url = urllib.parse.urljoin( self._phpcmplr_url, handler )
    parameters = self._TranslateRequest( request_data )
    parameters.update( query )
    body = ToBytes( json.dumps( parameters ) )
    extra_headers = self._ExtraHeaders( handler, body )

    self._logger.debug( 'Making PhpCmplr request: %s %s',
                        'POST', utils.ToUnicode( url ) )

    response = requests.request( native( bytes( b'POST' ) ),
                                 native( url ),
                                 data = body,
                                 headers = extra_headers )

    response.raise_for_status()
    return response.json()


  def _ExtraHeaders( self, handler, body ):
    extra_headers = { 'content-type': 'application/json' }
    return extra_headers


  def _TranslateRequest( self, request_data ):
    if not request_data:
      return {}

    path = request_data[ 'filepath' ]
    contents = request_data[ 'file_data' ][ path ][ 'contents' ]

    return {
      'files': [ {
        'path': path,
        'contents': contents,
      } ],
    }


  def _GetLocation( self, request_data ):
    path = request_data[ 'filepath' ]
    line = request_data[ 'line_num' ]
    column = request_data[ 'column_num' ]

    return {
      'path': path,
      'line': line,
      'col': column,
    }


  def ComputeCandidatesInner( self, request_data ):
    if not self._ServerIsRunning():
      return

    location = self._GetLocation( request_data )
    location[ 'col' ] = request_data[ 'start_column' ]
    completion_data = self._GetResponse( '/complete',
      { 'location': location },
      request_data )

    return [ self._MakeCompletion( data ) for data in completion_data[ 'completions' ] ]


  def _MakeCompletion( self, data ):
    return responses.BuildCompletionData(
      insertion_text = data[ 'insertion' ],
      menu_text = data[ 'display' ],
      extra_menu_info = data[ 'extended_display' ],
      kind = KINDS_MAP.get( data[ 'kind' ] ),
      detailed_info = data[ 'description' ] )


  def OnFileReadyToParse( self, request_data ):
    if not self._ServerIsRunning():
      return

    diagnostics_data = self._GetResponse( '/diagnostics',
      { 'path': request_data[ 'filepath' ] },
      request_data )

    diagnostics = []
    for diag in diagnostics_data[ 'diagnostics' ]:
      start = responses.Location( diag[ 'start' ][ 'line' ], diag[ 'start' ][ 'col' ], request_data[ 'filepath' ] )
      # `+ 1` is safe: translates from last byte of last included codepoint
      # to first byte of first not included codepoint.
      end = responses.Location( diag[ 'end' ][ 'line' ], diag[ 'end' ][ 'col' ] + 1, request_data[ 'filepath' ] )
      diagnostics.append( responses.Diagnostic(
        [],
        start,
        responses.Range( start, end ),
        diag[ 'description' ],
        'ERROR' ) )

    return [ responses.BuildDiagnosticData( d ) for d in diagnostics ]


  def _GoTo( self, request_data ):
    if not self._ServerIsRunning():
      return

    goto_data = self._GetResponse( '/goto',
      { 'location': self._GetLocation( request_data ) },
      request_data )

    response = [ responses.BuildGoToResponse( location[ 'path' ], location[ 'line' ], location[ 'col' ] )
      for location in goto_data[ 'goto' ] ]

    if len(response) == 0:
      return responses.BuildDisplayMessageResponse( 'Definition not found' )
    elif len(response) == 1:
      return response[ 0 ]
    return response


  def _GetType( self, request_data ):
    if not self._ServerIsRunning():
      return

    type_data = self._GetResponse( '/type',
      { 'location': self._GetLocation( request_data ) },
      request_data )

    response = type_data[ 'type' ]
    if not response:
      response = 'unknown'
    return responses.BuildDisplayMessageResponse( response )


  def _FixIt( self, request_data ):
    if not self._ServerIsRunning():
      return

    fix_data = self._GetResponse( '/fix',
      { 'location': self._GetLocation( request_data ) },
      request_data )

    return responses.BuildFixItResponse(
        [ self._MakeFixIt( fix ) for fix in fix_data[ 'fixes' ] ] )


  def _MakeFixIt( self, data ):
    text = data[ 'description' ]
    chunks = []
    for chunk_data in data[ 'chunks' ]:
      start = responses.Location(
          chunk_data[ 'start' ][ 'line' ],
          chunk_data[ 'start' ][ 'col' ],
          chunk_data[ 'start' ][ 'path' ] )
      # `+ 1` is safe: translates from last byte of last included codepoint
      # to first byte of first not included codepoint.
      end = responses.Location(
          chunk_data[ 'end' ][ 'line' ],
          chunk_data[ 'end' ][ 'col' ] + 1,
          chunk_data[ 'end' ][ 'path' ] )
      chunks.append( responses.FixItChunk(
          chunk_data[ 'replacement' ],
          responses.Range( start, end ) ) )

    return responses.FixIt( chunks[ 0 ].range.start_, chunks, text )


  def GetSubcommandsMap( self ):
    return {
      'StopServer'     : ( lambda self, request_data, args:
                           self.Shutdown() ),
      'RestartServer'  : ( lambda self, request_data, args:
                           self._RestartServer() ),
      'GoTo'           : ( lambda self, request_data, args:
                           self._GoTo( request_data ) ),
      'GetType'        : ( lambda self, request_data, args:
                           self._GetType( request_data ) ),
      'FixIt'          : ( lambda self, request_data, args:
                           self._FixIt( request_data ) ),
    }


  def DebugInfo( self, request_data ):
    with self._server_lock:
      phpcmplr_server = responses.DebugInfoServer(
        name = 'phpcmplr',
        handle = self._phpcmplr_phandle,
        executable = self._phpcmplr_path,
        address = '127.0.0.1',
        port = self._phpcmplr_port,
        logfiles = [ self._phpcmplr_stdout, self._phpcmplr_stderr ] )

      php_interpreter_item = responses.DebugInfoItem(
        key = 'PHP interpreter',
        value = self._php_path )

      return responses.BuildDebugInfoResponse(
        name = 'PHP',
        servers = [ phpcmplr_server ],
        items = [ php_interpreter_item ] )

