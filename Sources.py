#!/usr/bin/python
# CSS Test Source Manipulation Library
# Initial code by fantasai, joint copyright 2010 W3C and Microsoft
# Licensed under BSD 3-Clause: <http://www.w3.org/Consortium/Legal/2008/03-bsd-license>

from os.path import basename, exists, join
import os
import filecmp
import shutil
import re
import codecs
from xml import dom
import html5lib # Warning: This uses a patched version of html5lib
from html5lib import treebuilders, inputstream
from lxml import etree
from lxml.etree import ParseError
from Utils import getMimeFromExt, escapeToNamedASCII, basepath, isPathInsideBase, relativeURL, assetName
from mercurial import ui, hg

class SourceTree(object):
  """Class that manages structure of test repository source.
     Temporarily hard-coded path and filename rules, this should be configurable.
  """

  def __init__(self, repository):
    self.mTestExtensions = ['.xht', '.html', '.xhtml', '.htm', '.xml']
    self.mReferenceExtensions = ['.xht', '.html', '.xhtml', '.htm', '.xml', '.png', '.svg']
    self.mRepository = repository
  
  def _splitPath(self, filePath):
    """split a path into a list of directory names and the file name
       paths may come form the os or mercurial, which always uses '/' as the 
       directory separator
    """
    path, fileName = os.path.split(filePath.lower())
    if ('' == path):
      pathList = []
    elif ('/' in path):
      pathList = path.split('/')
    else:
      pathList = path.split(os.path.sep)
    return (pathList, fileName)
      
  def isTracked(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return ((not self._isIgnored(pathList, fileName)) and
            (self._isApprovedPath(pathList) or self._isSubmittedPath(pathList)))
      
  def _isApprovedPath(self, pathList):
    return (('approved' in pathList) and ('approved' == pathList[0]) and ('src' in pathList))
      
  def isApprovedPath(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return (not self._isIgnored(pathList, fileName)) and self._isApprovedPath(pathList)
      
  def _isSubmittedPath(self, pathList):
    return (('submitted' in pathList) and ('incoming' not in pathList))
  
  def isSubmittedPath(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return (not self._isIgnored(pathList, fileName)) and self._isSubmittedPath(pathList)
  
  def _isIgnored(self, pathList, fileName):
    return (('.hg' in pathList) or ('.svn' in pathList) or ('cvs' in pathList) or
            fileName.startswith('.directory') or ('lock' == fileName))
      
  def isIgnored(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return self._isIgnored(pathList, fileName)
      
  def _isSupportPath(self, pathList):
    return ('support' in pathList)
      
  def _isSupport(self, pathList, fileName):
    return (self._isSupportPath(pathList) or 
            ((not self._isReference(pathList, fileName)) and 
             (not self._isTestCase(pathList, fileName))))
      
  def isSupport(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return (not self._isIgnored(pathList, fileName)) and self._isSupport(pathList, fileName)
      
  def _isReferencePath(self, pathList):
    return (('reftest' in pathList) or ('reference' in pathList))
      
  def _isReference(self, pathList, fileName):
    if (not self._isSupportPath(pathList)):
      fileExt = os.path.splitext(fileName)[1]
      if (('-ref' in fileName) or fileName.startswith('ref-') or
           ('-notref' in fileName) or fileName.startswith('notref-')):
        return (fileExt in self.mReferenceExtensions)
      if (self._isReferencePath(pathList)):
        return (fileExt in self.mReferenceExtensions)
    return False    
      
  def isReference(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return (not self._isIgnored(pathList, fileName)) and self._isReference(pathList, fileName)
  
  def _isTestCase(self, pathList, fileName):
    if ((not self._isSupportPath(pathList)) and (not self._isReference(pathList, fileName))):
      fileExt = os.path.splitext(fileName)[1]
      return (fileExt in self.mTestExtensions)
    return False

  def isTestCase(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    return (not self._isIgnored(pathList, fileName)) and self._isTestCase(pathList, fileName)
      
  def getAssetName(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    if (self._isReference(pathList, fileName) or self._isTestCase(pathList, fileName)):
      return assetName(fileName)
    return fileName.lower() # support files keep full name

  def getAssetType(self, filePath):
    pathList, fileName = self._splitPath(filePath)
    if (self._isReference(pathList, fileName)):
      return 'reference'
    if (self._isTestCase(pathList, fileName)):
      return 'testcase'
    return 'support'
  

class SourceCache:
  """Cache for FileSource objects. Supports one FileSource object
     per sourcepath.
  """
  def __init__(self, sourceTree):
    self.__cache = {}
    self.sourceTree = sourceTree

  def generateSource(self, sourcepath, relpath, changeCtx=None):
    """Return a FileSource or derivative based on the extensionMap.

       Uses a cache to avoid creating more than one of the same object:
       does not support creating two FileSources with the same sourcepath;
       asserts if this is tried. (.htaccess files are not cached.)
       
       Cache is bypassed if loading form a change context
    """
    if ((None == changeCtx) and self.__cache.has_key(sourcepath)):
      source = self.__cache[sourcepath]
      assert relpath == source.relpath
      return source

    if basename(sourcepath) == '.htaccess':
      return ConfigSource(self.sourceTree, sourcepath, relpath, changeCtx)
    mime = getMimeFromExt(sourcepath)
    if (mime == 'application/xhtml+xml'):
      source = XHTMLSource(self.sourceTree, sourcepath, relpath, changeCtx)
    elif (mime == 'text/html'):
      source = HTMLSource(self.sourceTree, sourcepath, relpath, changeCtx)
    elif ((mime == 'application/xml') or (mime == 'image/svg+xml')):
      source = XMLSource(self.sourceTree, sourcepath, relpath, changeCtx)
    else:
      source = FileSource(self.sourceTree, sourcepath, relpath, mime, changeCtx)
    if (None == changeCtx):
      self.__cache[sourcepath] = source
    return source

class SourceSet:
  """Set of FileSource objects. No two FileSources in the set may
     have the same relpath (except .htaccess files, which are merged).
  """
  def __init__(self, sourceCache):
    self.sourceCache = sourceCache
    self.pathMap = {} # relpath -> source

  def __len__(self):
    return len(self.pathMap)
    
  def __contains__(self, source):
    return source.relpath in self.pathMap # XXX use source.name()


  def iter(self):
    """Iterate over FileSource objects in SourceSet.
    """
    return self.pathMap.itervalues()

  def addSource(self, source):
    """Add FileSource `source`. Throws exception if we already have
       a FileSource with the same path relpath but different contents.
       (ConfigSources are exempt from this requirement.)
    """
    cachedSource = self.pathMap.get(source.relpath) # XXX use source.name() 
    if not cachedSource:
      self.pathMap[source.relpath] = source
    else:
      if source != cachedSource:
        if isinstance(source, ConfigSource):
          cachedSource.append(source)
        else:
          raise Exception("File merge mismatch %s vs %s for %s" % \
                (cachedSource.sourcepath, source.sourcepath, source.relpath))

  def add(self, sourcepath, relpath):
    """Generate and add FileSource from sourceCache. Return the resulting
       FileSource.

       Throws exception if we already have a FileSource with the same path
       relpath but different contents.
    """
    source = self.sourceCache.generateSource(sourcepath, relpath)
    self.addSource(source)
    return source

  @staticmethod
  def combine(a, b):
    """Merges a and b, and returns whichever one contains the merger (which
       one is chosen based on merge efficiency). Can accept None as an argument.
    """
    if not (a and b):
      return a or b
    if len(a) < len(b):
      return b.merge(a)
    return a.merge(b)

  def merge(self, other):
    """Merge sourceSet's contents into this SourceSet.

       Throws a RuntimeError if there's a sourceCache mismatch.
       Throws an Exception if two files with the same relpath mismatch.
       Returns merge result (i.e. self)
    """
    if self.sourceCache is not other.sourceCache:
      raise RuntimeError

    for source in other.pathMap.itervalues():
      self.addSource(source)
    return self

  def write(self, format):
    """Write files out through OutputFormat `format`.
    """
    for source in self.pathMap.itervalues():
      format.write(source)


class StringReader(object):
  """Wrapper around a string to give it a file-like api
  """
  def __init__(self, string):
    self.mString = string
    self.mIndex = 0
  
  def read(self, maxSize = None):
    if (self.mIndex < len(self.mString)):
      if (maxSize and (0 < maxSize)):
        slice = self.mString[self.mIndex:self.mIndex + maxSize]
        self.mIndex += len(slice)
        return slice
      else:
        self.mIndex = len(self.mString)
        return self.mString
    return ''


class SourceMetaError(Exception):
  pass

class FileSource:
  """Object representing a file. Two FileSources are equal if they represent
     the same file contents. It is recommended to use a SourceCache to generate
     FileSources.
  """

  def __init__(self, sourceTree, sourcepath, relpath, mimetype=None, changeCtx=None):
    """Init FileSource from source path. Give it relative path relpath.

       `mimetype` should be the canonical MIME type for the file, if known.
        If `mimetype` is None, guess type from file extension, defaulting to
        the None key's value in extensionMap.
        
       `changeCtx` if provided, is a mercurial change context. When present,
        all file reads will be done from the change context instead.
    """
    self.sourceTree = sourceTree
    self.sourcepath = sourcepath
    self.relpath    = relpath
    self.mimetype   = mimetype or getMimeFromExt(sourcepath)
    self.changeCtx  = changeCtx
    self.error      = None
    self.encoding   = 'utf-8'
    self.refs       = {}
    self.metadata   = None
    self.metaSource = None

  def __eq__(self, other):
    if not isinstance(other, FileSource):
      return False
    return self.sourcepath == other.sourcepath or \
           filecmp.cmp(self.sourcepath, other.sourcepath)

  def __ne__(self, other):
    return not self == other

  def __cmp__(self, other):
    return cmp(self.name(), other.name())

  def name(self):
    return self.sourceTree.getAssetName(self.sourcepath)

  def relativeURL(self, other):
    return relativeURL(self.relpath, other.relpath)
    
  def data(self):
    """Return file contents as a byte string."""
    if (self.changeCtx):
      data = self.changeCtx.filectx(self.sourcepath).data()
    else:
      data = open(self.sourcepath, 'r').read()
    if (data.startswith(codecs.BOM_UTF8)):
      self.encoding = 'utf-8-sig' # XXX look for other unicode BOMs
    return data
    
  def unicode(self):
    try:
      return self.data().decode(self.encoding)
    except UnicodeDecodeError, e:
      return None
    
  def parse(self):
    """Parses and validates FileSource data from sourcepath."""
    self.loadMetadata()

  def validate(self):
    """Ensure data is loaded from sourcepath."""
    self.parse()

  def adjustContentPaths(self, format):
    """Adjust any paths in file content for output format
       XXX need to account for group paths"""
    for refType, refPath, refNode, refSource in self.refs.itervalues():
      if refSource:
        refPath = relativeURL(format.dest(self.relpath), format.dest(refSource.relpath))
      else:
        refPath = relativeURL(format.dest(self.relpath), format.dest(refPath))
      if (refPath != refNode.get('href')):
        refNode.set('href', refPath)
    
  def write(self, format):
    """Writes FileSource.data() out to `self.relpath` through Format `format`."""
    data = self.data()
    f = open(format.dest(self.relpath), 'w')
    f.write(data)
    if (self.metaSource):
      self.metaSource.write(format) # XXX need to get output path from format, but not let it choose actual format

  def compact(self):
    """Clears all cached data, preserves computed data."""
    pass

  def revisionOf(self, path, relpath=None):
    """Get last committed mercurial revision of file.
       Accepts optional relative path to target.
    """
    if relpath:
      path = os.path.join(os.path.dirname(path), relpath)
    repo = self.sourceTree.mRepository
    fctx = repo.filectx(path, fileid = repo.file(path).tip())
    return fctx.rev() + 1   # svn starts at 1, hg starts at 0 - XXX return revision number for now, eventually switch to changeset id and date

  def revision(self):
    """Returns svn revision number of last commit to this file or any related file, references, support files, etc.
       XXX also needs to account for .meta file
    """
    revision = self.revisionOf(self.sourcepath)
    for refName in self.refs:
      refPath = self.refs[refName][1]
      refRevision = self.revisionOf(self.sourcepath, refPath) # XXX also follow reference chains
      if revision < refRevision:
        revision = refRevision
    return revision

  def loadMetadata(self):
    """Look for .meta file and load any metadata from it if present
    """
    pass
    
  def augmentMetadata(self, next=None, prev=None, reference=None, notReference=None):
    if (self.metaSource):
      return self.metaSource.augmentMetadata(next, prev, reference, notReference)
    return None
    
  # See http://wiki.csswg.org/test/css2.1/format for more info on metadata
  def getMetadata(self, asUnicode = False):
    """Return dictionary of test metadata. Returns None and stores error
       exception in self.error if there is a parse or metadata error.
       Data fields include:
         - asserts [list of strings]
         - credits [list of (name string, url string) tuples]
         - reviewers [ list of (name string, url string) tuples]
         - flags   [list of token strings]
         - links   [list of url strings]
         - name    [string]
         - title   [string]
         - references [list of (reftype, relpath) per reference; None if not reftest]
         - revision   [revision id of last commit]
         - selftest [bool]
       Strings are given in UTF-8 unless asUnicode==True.
    """
    
    self.validate()

    if self.error:
      return None

    def encode(str):
      return intern(str.encode('utf-8'))

    def escape(str, andIntern = True):
      return str.encode('utf-8') if asUnicode else intern(escapeToNamedASCII(str)) if andIntern else escapeToNamedASCII(str)

    references = None
    usedRefs = {}
    usedRefs[self.name()] = '=='
    def listReferences(source):
      for refType, refPath, refNode, refSource in source.refs.values():
        refName = refSource.name() if refSource else self.sourceTree.getAssetName(join(self.sourcepath, refPath))
        if (refName not in usedRefs):
          usedRefs[refName] = refType
          if (refSource):
            references.append({'type': refType, 'relpath': self.relativeURL(refSource)})
            if ('==' == refType): # XXX don't follow != refs for now (until we export proper ref trees)
              listReferences(refSource)
          else:
            references.append({'type': refType, 'relpath': refPath, 'name': refName})
    if (self.refs):
      references = []
      listReferences(self)

    if (self.metadata):
      data = {'asserts'   : [escape(assertion, False) for assertion in self.metadata['asserts']],
              'credits'   : [(escape(name), encode(link)) for name, link in self.metadata['credits']],
              'reviewers' : [(escape(name), encode(link)) for name, link in self.metadata['reviewers']],
              'flags'     : [encode(flag) for flag in self.metadata['flags']],
              'links'     : [encode(link) for link in self.metadata['links']],
              'name'      : encode(self.name()),
              'title'     : escape(self.metadata['title'], False),
              'references': references,
              'revision'  : self.revision(),
              'selftest'  : self.isSelftest()
             }
      return data
    return None

  def addReference(self, referenceSource, match = None):
    """Add reference source."""
    self.validate()
    refName = referenceSource.name()
    refPath = self.relativeURL(referenceSource)
    if refName not in self.refs:
      node = None
      if match == '==':
        node = self.augmentMetadata(reference=referenceSource).reference
      elif match == '!=':
        node = self.augmentMetadata(notReference=referenceSource).notReference
      self.refs[refName] = (match, refPath, node, referenceSource)
    else:
      node = self.refs[refName][2]
      node.set('href', refPath)
      if (match):
        node.set('rel', 'mismatch' if ('!=' == match) else 'match')
      else:
        match = self.refs[refName][0]
      self.refs[refName] = (match, refPath, node, referenceSource)

  def getReferencePaths(self):
    """Get list of paths to references as tuple(path, relPath, refType)."""
    self.validate()
    return [(os.path.join(os.path.dirname(self.sourcepath), ref[1]), 
             os.path.join(os.path.dirname(self.relpath), ref[1]),
             ref[0]) 
            for ref in self.refs.values()]
    
  def isTest(self):
    self.validate()
    return bool(self.metadata) and bool(self.metadata.get('links'))
    
  def isReftest(self):
    return self.isTest() and bool(self.refs)

  def isSelftest(self):
    return self.isTest() and (not bool(self.refs))
        
  def hasFlag(self, flag):
    data = self.getMetadata()
    if data:
      return flag in data['flags']
    return False


    
class ConfigSource(FileSource):
  """Object representing a text-based configuration file.
     Capable of merging multiple config-file contents.
  """

  def __init__(self, sourceTree, sourcepath, relpath, mimetype=None, changeCtx=None):
    """Init ConfigSource from source path. Give it relative path relpath.
    """
    FileSource.__init__(self, sourceTree, sourcepath, relpath, mimetype, changeCtx)
    self.sourcepath = [sourcepath]

  def __eq__(self, other):
    if not isinstance(other, ConfigSource):
      return False
    if self is other or self.sourcepath == other.sourcepath:
      return True
    if len(self.sourcepath) != len(other.sourcepath):
      return False
    for this, that in zip(self.sourcepath, other.sourcepath):
      if not filecmp.cmp(this, that):
        return False
    return True

  def __ne__(self, other):
    return not self == other

  def name(self):
    return '.htaccess'
    
  def data(self):
    """Merge contents of all config files represented by this source."""
    data = ''
    for src in self.sourcepath:
      data += open(src).read()
      data += '\n'
    return data
    
  def append(self, other):
    """Appends contents of ConfigSource `other` to this source.
       Asserts if self.relpath != other.relpath.
    """
    assert isinstance(other, ConfigSource)
    assert self != other and self.relpath == other.relpath
    self.sourcepath.extend(other.sourcepath)

class ReftestFilepathError(Exception):
  pass

class ReftestManifest(ConfigSource):
  """Object representing a reftest manifest file.
     Iterating the ReftestManifest returns (testpath, refpath) tuples
     with paths relative to the manifest.
  """
  def __init__(self, sourceTree, sourcepath, relpath, changeCtx=None):
    """Init ReftestManifest from source path. Give it relative path `relpath`
       and load its .htaccess file.
    """
    ConfigSource.__init__(self, sourceTree, sourcepath, relpath, mimetype = 'config/reftest', changeCtx = changeCtx)

  def basepath(self):
    """Returns the base relpath of this reftest manifest path, i.e.
       the parent of the manifest file.
    """
    return basepath(self.relpath)

  baseRE = re.compile(r'^#\s*relstrip\s+(\S+)\s*')
  stripRE = re.compile(r'#.*')
  parseRE = re.compile(r'^\s*([=!]=)\s*(\S+)\s+(\S+)')

  def __iter__(self):
    """Parse the reftest manifest files represented by this ReftestManifest
       and return path information about each reftest pair as
         ((test-sourcepath, ref-sourcepath), (test-relpath, ref-relpath), reftype)
       Raises a ReftestFilepathError if any sources file do not exist or
       if any relpaths point higher than the relpath root.
    """
    striplist = []
    for src in self.sourcepath:
      relbase = basepath(self.relpath)
      srcbase = basepath(src)
      for line in open(src):
        strip = self.baseRE.search(line)
        if strip:
          striplist.append(strip.group(1))
        line = self.stripRE.sub('', line)
        m = self.parseRE.search(line)
        if m:
          record = ((join(srcbase, m.group(2)), join(srcbase, m.group(3))), \
                    (join(relbase, m.group(2)), join(relbase, m.group(3))), \
                    m.group(1))
#          for strip in striplist:
            # strip relrecord
          if not exists(record[0][0]):
            raise ReftestFilepathError("Manifest Error in %s: "
                                       "Reftest test file %s does not exist." \
                                        % (src, record[0][0]))
          elif not exists(record[0][1]):
            raise ReftestFilepathError("Manifest Error in %s: "
                                       "Reftest reference file %s does not exist." \
                                       % (src, record[0][1]))
          elif not isPathInsideBase(record[1][0]):
            raise ReftestFilepathError("Manifest Error in %s: "
                                       "Reftest test replath %s not within relpath root." \
                                       % (src, record[1][0]))
          elif not isPathInsideBase(record[1][1]):
            raise ReftestFilepathError("Manifest Error in %s: "
                                       "Reftest test replath %s not within relpath root." \
                                       % (src, record[1][1]))
          yield record

import Utils # set up XML catalog
xhtmlns = '{http://www.w3.org/1999/xhtml}'

class XMLSource(FileSource):
  """FileSource object with support reading XML trees."""

  # Public Data
  syntaxErrorDoc = \
  u"""
  <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
  <html xmlns="http://www.w3.org/1999/xhtml">
    <head><title>Syntax Error</title></head>
    <body>
      <p>The XML file <![CDATA[%s]]> contains a syntax error and could not be parsed.
      Please correct it and try again.</p>
      <p>The parser's error report was:</p>
      <pre><![CDATA[%s]]></pre>
    </body>
  </html>
  """

  # Private Data and Methods
  __parser = etree.XMLParser(no_network=True,
  # perf nightmare           dtd_validation=True,
                             remove_comments=False,
                             strip_cdata=False,
                             resolve_entities=False)

  # Public Methods

  def __init__(self, sourceTree, sourcepath, relpath, changeCtx=None):
    """Initialize XMLSource by loading from XML file `sourcepath`.
      Parse errors are reported as caught exceptions in `self.error`,
      and the source is replaced with an XHTML error message.
    """
    FileSource.__init__(self, sourceTree, sourcepath, relpath, changeCtx = changeCtx)
    self.tree = None
    self.injectedTags = {}

  def cacheAsParseError(self, filename, e):
      """Replace document with an error message."""
      errorDoc = self.syntaxErrorDoc % (filename, e)
      from StringIO import StringIO 
      self.tree = etree.parse(StringIO(errorDoc), parser=self.__parser)

  def parse(self):
    """Parse file and store any parse errors in self.error"""
    self.error = False
    try:
      self.tree = etree.parse(StringReader(self.data()), parser=self.__parser)
      self.encoding = self.tree.docinfo.encoding or 'utf-8'
      self.injectedTags = {}

      FileSource.loadMetadata(self)
      if ((not self.metadata) and self.tree and (not self.error)):
        self.extractMetadata(self.tree)
    except etree.ParseError, e:
      self.cacheAsParseError(self.sourcepath, e)
      e.W3CTestLibErrorLocation = self.sourcepath
      self.error = e
      self.encoding = 'utf-8'
      
  def validate(self):
    """Parse file if not parsed, and store any parse errors in self.error"""
    if self.tree is None:
      self.parse()

  def injectHeadLink(self, rel, href, tagCode = None):
    """Inject (prepend) <link> with data given in inside head. 
       Injected element is tagged with `tagCode`, which can be
       used to clear it with clearInjectedTags later.
    """
    self.validate()
    head = self.tree.getroot().find(xhtmlns+'head')
    if (head):
      node = etree.Element('link', {'rel': rel, 'href': href})
      node.tail = head.text
      head.insert(0, node)
      self.injectedTags[node] = tagCode or True
      return node
    return None

  def clearInjectedTags(self, tagCode = None):
    """Clears all injected elements from the tree, or clears injected
       elements tagged with `tagCode` if `tagCode` is given.
    """
    if not self.injectedTags or not self.tree: return
    for node in self.injectedTags:
      node.getparent().remove(node)
      del self.injectedTags[node]

  def serializeXML(self):
    self.validate()
    return etree.tounicode(self.tree)

  def data(self):
    if ((not self.tree) or (self.metaSource)):
      return FileSource.data(self)
    return self.serializeXML().encode(self.encoding, 'xmlcharrefreplace')
    
  def unicode(self):
    if ((not self.tree) or (self.metaSource)):
      return FileSource.unicode(self)
    return self.serializeXML()
    
  def write(self, format, output=None):
    """Write Source through OutputFormat `format`.
       Write contents as string `output` instead if specified.
    """
    if not output:
      output = self.unicode()

    # write
    f = open(format.dest(self.relpath), 'w')
    f.write(output.encode(self.encoding, 'xmlcharrefreplace'))
    f.close()

  def compact(self):
    self.tree = None


  def getHeadElements(self, tree):
    head = tree.getroot().find(xhtmlns+'head')
    if (None != head):
      return [node for node in head]
    return None
  
  
  def extractMetadata(self, tree):
    """Extract metadata from tree."""
    links = []; credits = []; reviewers = []; flags = []; asserts = [];
    self.metadata = {'asserts'   : asserts,
                     'credits'   : credits,
                     'reviewers' : reviewers,
                     'flags'     : flags,
                     'links'     : links,
                     'title'     : ''
                    }

    def tokenMatch(token, string):
      if not string: return False
      return bool(re.search('(^|\s+)%s($|\s+)' % token, string))

    readFlags = False
    head = self.getHeadElements(tree)
    try:
      if (head == None): raise SourceMetaError("Missing <head> element")
      # Scan and cache metadata
      for node in head:
        if (node.tag == xhtmlns+'link'):
          # help links
          if tokenMatch('help', node.get('rel')):
            link = node.get('href').strip()
            if not link:
              raise SourceMetaError("Help link missing href value.")
            if not link.startswith('http://') or link.startswith('https://'):
              raise SourceMetaError("Help link must be absolute URL.")
            if link.find('propdef') == -1:    # XXX Remove this
              links.append(link)
          # == references
          elif tokenMatch('match', node.get('rel')) or tokenMatch('reference', node.get('rel')):
            refPath = node.get('href').strip()
            if not refPath:
              raise SourceMetaError("Reference link missing href value.")
            refName = self.sourceTree.getAssetName(join(self.sourcepath, refPath))
            if (refName in self.refs):
              raise SourceMetaError("Reference already specified.")
            self.refs[refName] = ('==', refPath, node, None)
          # != references
          elif tokenMatch('mismatch', node.get('rel')) or tokenMatch('not-reference', node.get('rel')):
            refPath = node.get('href').strip()
            if not refPath:
              raise SourceMetaError("Reference link missing href value.")
            refName = self.sourceTree.getAssetName(join(self.sourcepath, refPath))
            if (refName in self.refs):
              raise SourceMetaError("Reference already specified.")
            self.refs[refName] = ('!=', refPath, node, None)
          else: # may have both author and reviewer in the same link
            # credits
            if tokenMatch('author', node.get('rel')):
              name = node.get('title')
              name = name.strip() if name else name
              if not name:
                raise SourceMetaError("Author link missing name (title attribute).")
              link = node.get('href').strip()
              if not link:
                raise SourceMetaError("Author link missing contact URL (http or mailto).")
              credits.append((name, link))
            # reviewers
            if tokenMatch('reviewer', node.get('rel')):
              name = node.get('title')
              name = name.strip() if name else name
              if not name:
                raise SourceMetaError("Reviewer link missing name (title attribute).")
              link = node.get('href').strip()
              if not link:
                raise SourceMetaError("Reviewer link missing contact URL (http or mailto).")
              reviewers.append((name, link))
        elif node.tag == xhtmlns+'meta':
          metatype = node.get('name')
          metatype = metatype.strip() if metatype else metatype
          # requirement flags
          if metatype == 'flags':
            if readFlags:
              raise SourceMetaError("Flags must only be specified once.")
            readFlags = True
            for flag in sorted(node.get('content').split()):
              flags.append(flag)
          # test assertions
          elif metatype == 'assert':
            asserts.append(node.get('content').strip().replace('\t', ' '))
        # test title
        elif node.tag == xhtmlns+'title':
          title = node.text.strip() if node.text else ''
          match = re.match('(?:[^:]*)[tT]est(?:[^:]*):(.*)', title)
          if (match):
            title = match.group(1)
          self.metadata['title'] = title.strip()

    # Cache error and return
    except SourceMetaError, e:
      e.W3CTestLibErrorLocation = self.sourcepath
      self.error = e

  def augmentMetadata(self, next=None, prev=None, reference=None, notReference=None):
     """Add extra useful metadata to the head. All arguments are optional.
          * Adds next/prev links to  next/prev Sources given
          * Adds reference link to reference Source given
     """
     self.validate()
     if next:
       next = self.injectHeadLink('next', self.relativeURL(next), 'next')
     if prev:
       prev = self.injectHeadLink('prev', self.relativeURL(prev), 'prev')
     if reference:
       reference = self.injectHeadLink('match', self.relativeURL(reference), 'ref')
     if notReference:
       notReference = self.injectHeadLink('mismatch', self.relativeURL(notReference), 'not-ref')
     NodeTuple = collections.namedtuple('NodeTuple', ['next', 'prev', 'reference', 'notReference'])
     return NodeTuple(next, prev, reference, notReference)


class XHTMLSource(XMLSource):
  """FileSource object with support for XHTML->HTML conversions."""

  # Public Methods

  def __init__(self, sourceTree, sourcepath, relpath, changeCtx=None):
    """Initialize XHTMLSource by loading from XHTML file `sourcepath`.
      Parse errors are reported as caught exceptions in `self.error`,
      and the source is replaced with an XHTML error message.
    """
    XMLSource.__init__(self, sourceTree, sourcepath, relpath, changeCtx = changeCtx)

  def serializeXHTML(self):
    return self.serializeXML()

  def serializeHTML(self):
    self.validate()
    # Serialize
    o = html5lib.serializer.serialize(self.tree, tree='lxml',
                                      format='html',
                                      emit_doctype='html',
                                      lang_attr='html',
                                      resolve_entities=False,
                                      escape_invisible='named',
                                      omit_optional_tags=False,
                                      minimize_boolean_attributes=False,
                                      quote_attr_values=True)

    # lxml fixup for eating whitespace outside root element
    m = re.search('<!DOCTYPE[^>]+>(\s*)<', o)
    if m.group(1) == '': # match first to avoid perf hit from searching whole doc
      o = re.sub('(<!DOCTYPE[^>]+>)<', '\g<1>\n<', o)
    return o


class NodeWrapper(object):
  """Wrapper object for dom nodes to give them an etree-like api
  """
  
  def __init__(self, node):
    self.node = node
    self.tag = xhtmlns + self.node.tagName
    self.text = ''
    child = node.firstChild
    while (None != child):
      if (child.nodeType == dom.Node.TEXT_NODE):
        self.text += child.data
      child = child.nextSibling
    
  def getparent(self):
    return NodeWrapper(self.node.parentNode)
    
  def remove(self, child):
    self.node.removeChild(child.node)
    
  def get(self, attr):
    if (self.node.hasAttribute(attr)):
      return self.node.getAttribute(attr)
    return None
    
  def set(self, attr, value):
    self.node.setAttribute(attr, value)
  
  
class HTMLSource(XMLSource):
  """FileSource object with support for HTML metadata and HTML->XHTML conversions (untested)."""

  # Private Data and Methods
  __parser = html5lib.HTMLParser(tree = treebuilders.getTreeBuilder('dom')) 
    # XXX it would be great to use lxml etree, but it chokes on some of our tests...
    # if that would work, the NodeWrapper and injectHeadLink below could be removed

  # Public Methods

  def __init__(self, sourceTree, sourcepath, relpath, changeCtx=None):
    """Initialize HTMLSource by loading from HTML file `sourcepath`.
    """
    XMLSource.__init__(self, sourceTree, sourcepath, relpath, changeCtx = changeCtx)

  def parse(self):
    """Parse file and store any parse errors in self.error"""
    self.error = False
    try:
      data = self.data()
      htmlStream = html5lib.inputstream.HTMLInputStream(StringReader(data))
      self.encoding = htmlStream.detectEncoding()[0]
      self.tree = self.__parser.parse(StringReader(data), encoding = self.encoding)
      self.injectedTags = {}

      FileSource.loadMetadata(self)
      if ((not self.metadata) and self.tree and (not self.error)):
        self.extractMetadata(self.tree)
    except etree.ParseError, e:
      e.W3CTestLibErrorLocation = self.sourcepath
      self.error = e
      self.encoding = 'utf-8'
      
  def serializeXHTML(self):
    self.validate()
    # Serialize
    o = html5lib.serializer.serialize(self.tree, tree='dom',
                                      format='xhtml',
                                      emit_doctype='xhtml',
                                      lang_attr='html',
                                      resolve_entities=False,
                                      escape_invisible='named',
                                      omit_optional_tags=False,
                                      minimize_boolean_attributes=False,
                                      quote_attr_values=True)

  def serializeHTML(self):
    self.validate()
    # Serialize
    o = html5lib.serializer.serialize(self.tree, tree='dom',
                                      format='html',
                                      emit_doctype='html',
                                      lang_attr='html',
                                      resolve_entities=False,
                                      escape_invisible='named',
                                      omit_optional_tags=False,
                                      minimize_boolean_attributes=False,
                                      quote_attr_values=True)

    # lxml fixup for eating whitespace outside root element
    m = re.search('<!DOCTYPE[^>]+>(\s*)<', o)
    if m.group(1) == '': # match first to avoid perf hit from searching whole doc
      o = re.sub('(<!DOCTYPE[^>]+>)<', '\g<1>\n<', o)
    return o

  def data(self):
    if ((not self.tree) or (self.metaSource)):
      return FileSource.data(self)
    return self.serializeHTML().encode(self.encoding, 'xmlcharrefreplace')
    
  def unicode(self):
    if ((not self.tree) or (self.metaSource)):
      return FileSource.unicode(self)
    return self.serializeHTML()
    
  def injectHeadLink(self, rel, href, tagCode = None):
    """Inject (prepend) <link> data given in head. 
       Injected element is tagged with `tagCode`, which can be
       used to clear it with clearInjectedTags later.
    """
    node = self.tree.createElement('link')
    node.setAttribute('rel', rel)
    node.setAttribute('href', href)
    self.validate()
    
    head = self.tree.getElementsByTagName('head').item(0)
    head.insertBefore(node, head.firstChild)
    node = NodeWrapper(node)
    self.injectedTags[node] = tagCode or True
    return node

  def getHeadElements(self, tree):
    head = self.tree.getElementsByTagName('head').item(0)
    if (None != head):
      elements = []
      node = head.firstChild
      while (None != node):
        if (node.nodeType == dom.Node.ELEMENT_NODE):
          elements.append(NodeWrapper(node))
        node = node.nextSibling
      return elements
    return None


