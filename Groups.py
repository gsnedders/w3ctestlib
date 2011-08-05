#!/usr/bin/python
# CSS Test Suite Manipulation Library
# Initial code by fantasai, joint copyright 2010 W3C and Microsoft
# Licensed under BSD 3-Clause: <http://www.w3.org/Consortium/Legal/2008/03-bsd-license>

import shutil
import filecmp
import os.path
import Utils
from os.path import exists, join
from Sources import SourceCache, SourceSet, ConfigSource, ReftestManifest, TestSource
from Utils import listfiles

excludeDirs = ['CVS', '.svn', '.hg']

class TestGroup:
  """Base class for test groups. Should never be used directly.
  """

  @staticmethod
  def combine(groupA, groupB):
    """Merge TestGroup `groupB` into `groupA`. Return the result of the merge.
       Can accept none as arguments.
    """
    if groupA and groupB:
      groupA.merge(groupB)
    return groupA or groupB

  def __init__(self, sourceCache, importDir, name=None, title=None, **kwargs):
    """Initialize with:
         SourceCache `sourceCache`
         Group name `name`, which must be a possible directory name or None
         Directory path `importDir`, whose context is imported into the group
         Option: Tuple of support directory names `supportDirNames` defaults
                 to ('support',).
         Kwarg: File path manifestPath relative to `importDir` that
           identifies the reftest manifest file (usually called 'reftest.list').
         Kwarg: File path manifestDest as destination (relative) path for
                the reftest manifest file. Defaults to value of manifestPath.
         If manifest provided, assumes that only the files listed in the manifest,
         the .htaccess files in its parent directory, and the `importDir`'s
         .htaccess file and support directory are relevant to the test suite.
    """
    assert exists(importDir), "Directory to import %s does not exist" % importDir

    # Save name
    self.name = name
    self.title = title
    
    sourceTree = sourceCache.sourceTree

    # Load htaccess
    htapath = join(importDir, '.htaccess')
    self.htaccess = ConfigSource(htapath, '.htaccess') \
                    if exists(htapath) else None

    # Load support files
    self.support = SourceSet(sourceCache)
    supportDirNames = kwargs.get('supportDirNames', ('support',))
    for supportName in supportDirNames:
      supportDir = join(importDir, supportName)
      if exists(supportDir):
        for (root, dirs, files) in os.walk(supportDir):
          for dir in excludeDirs:
            if dir in dirs:
              dirs.remove(dir)
          for name in files:
            sourcepath = join(root, name)
            relpath = Utils.relpath(sourcepath, importDir)
            self.support.add(sourcepath, relpath)

    # Load tests
    self.tests = SourceSet(sourceCache)
    self.refs  = SourceSet(sourceCache)

    # Read manifest
    manifestPath = kwargs.get('manifestPath', None)
    manifestDest = kwargs.get('manifestDest', manifestPath)
    if (manifestPath):
      self.manifest = ReftestManifest(join(importDir, manifestPath), manifestDest)

      # Import tests
      for (testSrc, refSrc), (testRel, refRel), refType in self.manifest:
        test = sourceCache.generateSource(testSrc, testRel, True)
        ref = sourceCache.generateSource(refSrc, refRel, True)
        test.addReference(ref, refType)
        self.tests.addSource(test)
    else:
      self.manifest = None
      # Import tests
      fileNameList = []
      if kwargs.get('selfTestExt'):
        fileNameList += listfiles(importDir, kwargs['selfTestExt'])
      if kwargs.get('selfTestList'):
        fileNameList += kwargs['selfTestList']
      for fileName in fileNameList:
        filePath = join(importDir, fileName)
        if sourceTree.isTestCase(filePath):
          test = sourceCache.generateSource(filePath, fileName, True)
          if (test.isTest()):
            self.tests.addSource(test)

    for test in self.tests.iter():
      if (test.isReftest()):
        usedRefs = {}
        usedRefs[test.name()] = '=='
        def loadReferences(source): # XXX need to verify refType for mutual exclusion (ie: a == b != a)
          for refSrcPath, refRelPath, refType in source.getReferencePaths():
            ref = sourceCache.generateSource(refSrcPath, refRelPath, True)
            source.addReference(ref)
            if (ref.name() not in usedRefs):
              usedRefs[ref.name()] = refType
              if (ref not in self.tests):
                self.refs.addSource(ref)
              if (isinstance(ref, TestSource)):
                loadReferences(ref)
        loadReferences(test)


  def sourceCache(self):
    return self.support.sourceCache

  def count(self):
    """Returns number of tests.
    """
    return len(self.tests)

  def iterTests(self):
    return self.tests.iter()

  def _initFrom(self, group=None):
    """Initialize with data from TestGroup `group`."""
    # copy
    self.name     = group.name if group else None
    self.title    = group.title if group else None
    self.htaccess = group.htaccess if group else None
    self.support  = group.support if group else None
    self.tests    = group.tests if grou else None

  def merge(self, other):
    """Merge Group `other`'s contents into this Group and clear its contents.
    """
    assert isinstance(other, TestGroup), \
           "Expected Group instance, got %s" % type(other)
    if self.htaccess and other.htaccess:
      self.htaccess.append(other.htaccess)
    else:
      self.htaccess = self.htaccess or other.htaccess
    other.htaccess = None

    self.support = SourceSet.combine(self.support, other.support)
    other.support = None

    self.tests = SourceSet.combine(self.tests, other.tests)
    other.tests = None
    
    self.refs  = SourceSet.combine(self.refs, other.refs)
    other.refs = None
    if self.manifest and other.manifest:
      self.manifest.append(other.manifest)
    else:
      self.manifest = self.manifest or other.manifest
    other.manifest = None
    

  def build(self, format):
    """Build Group's contents through OutputFormat `format`.
    """
    format.setSubDir(self.name)

    # Write .htaccess
    if self.htaccess:
      format.write(self.htaccess)

    # Write support files
    self.support.write(format)

    # Write tests
    self.tests.write(format)

    # Write refs
    self.refs.write(format)
    if self.manifest:
      format.write(self.manifest)

    format.setSubDir()

