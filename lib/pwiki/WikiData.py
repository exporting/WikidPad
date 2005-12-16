"""

Used terms:
    
    wikiword -- a string matching one of the wiki word regexes
    page -- real existing content stored and associated with a wikiword
            (which is the page name). Sometimes page is synonymous for page name
    alias -- wikiword without content but associated to a page name.
            For the user it looks as if the content of the alias is the content
            of the page for the associated page name
    defined wiki word -- either a page name or an alias
"""



from os import mkdir, unlink, rename    # listdir
from os.path import exists, join, basename
from time import time, localtime
import datetime
import re, string, glob

import gadfly
import DbStructure
from DbStructure import createWikiDB, WikiDBExistsException

# from WikiFormatting import FormatTypes

from WikiExceptions import *   # TODO make normal import?
import SearchAndReplace

from StringOps import mbcsEnc, mbcsDec, utf8Enc, utf8Dec, BOM_UTF8, \
        fileContentToUnicode, Tokenizer

import WikiFormatting


CleanTextRE = re.compile("[^A-Za-z0-9]")  # ?

class WikiData:
    "Interface to wiki data."
    def __init__(self, pWiki, dataDir):
        self.pWiki = pWiki
        self.dataDir = dataDir
        self.connWrap = None
        self.cachedWikiWords = None
        
        self._updateTokenizer = \
                Tokenizer(WikiFormatting.CombinedUpdateRE, -1)

        self._reinit()
        

    def _reinit(self):
        """
        Actual initialization or reinitialization after rebuildWiki()
        """
        conn = gadfly.gadfly("wikidb", self.dataDir)
        self.connWrap = DbStructure.ConnectWrap(conn)
        
        formatcheck, formatmsg = DbStructure.checkDatabaseFormat(self.connWrap)

        if formatcheck == 2:
            # Unknown format
            raise WikiDataException, formatmsg

        # Update database from previous versions if necessary
        if formatcheck == 1:
            try:
                DbStructure.updateDatabase(self.connWrap)
            except:
                self.connWrap.rollback()
                raise


        # create word caches
        self.cachedWikiWords = {}
        for word in self.getAllDefinedPageNames():
            self.cachedWikiWords[word] = 1

        # cache aliases
        aliases = self.getAllAliases()
        for alias in aliases:
            self.cachedWikiWords[alias] = 2

        self.cachedGlobalProps = None
        self.getGlobalProperties()

#         # maintenance
#         #self.execSql("delete from wikiwords where word = ''")
#         #self.execSql("delete from wikirelations where word = 'MyContacts'")
# 
#         # database versioning...
#         indices = self.execSqlQuerySingleColumn("select INDEX_NAME from __indices__")
#         tables = self.execSqlQuerySingleColumn("select TABLE_NAME from __table_names__")
# 
#         if "WIKIWORDPROPS_PKEY" in indices:
#             print "dropping index wikiwordprops_pkey"
#             self.execSql("drop index wikiwordprops_pkey")
#         if "WIKIWORDPROPS_WORD" not in indices:
#             print "creating index wikiwordprops_word"
#             self.execSql("create index wikiwordprops_word on wikiwordprops(word)")
#         if "WIKIRELATIONS_WORD" not in indices:
#             print "creating index wikirelations_word"
#             self.execSql("create index wikirelations_word on wikirelations(word)")
#         if "REGISTRATION" in tables:
#             self.execSql("drop table registration")

    # ---------- Direct handling of page data ----------
    
    def getAllDefinedPageNames(self):
        "get the names of all the pages in the db, no aliases"
        return self.execSqlQuerySingleColumn("select word from wikiwords")

    # TODO More general Wikiword to filename mapping
    def getAllPageNamesFromDisk(self):   # Used for rebuilding wiki
        files = glob.glob(join(mbcsEnc(self.dataDir)[0], '*.wiki'))
        return [mbcsDec(basename(file).replace('.wiki', ''), "replace")[0] for file in files]

    # TODO More general Wikiword to filename mapping
    def getWikiWordFileName(self, wikiWord):
        # return mbcsEnc(join(self.dataDir, "%s.wiki" % wikiWord))[0]
        return join(self.dataDir, u"%s.wiki" % wikiWord)

    def isDefinedWikiWord(self, word):
        "check if a word is a valid wikiword (page name or alias)"
        return self.cachedWikiWords.has_key(word)
    
    def getContent(self, word):
        if (not exists(self.getWikiWordFileName(word))):
            raise WikiFileNotFoundException, u"wiki page not found for word: %s" % word

        fp = open(self.getWikiWordFileName(word), "rU")
        content = fp.read()
        fp.close()

        return fileContentToUnicode(content)

        # TODO Remove method
    def _updatePageEntry(self, word, moddate = None, creadate = None):
        """
        Update/Create entry with additional information for a page
            (modif./creation date).
        Not part of public API!
        """
        ti = time()
        if moddate is None:
            moddate = ti
            
        data = self.execSqlQuery("select word from wikiwords where word = ?",
                (word,))
        if len(data) < 1:
            if creadate is None:
                creadate = ti
                
            self.execSql("insert into wikiwords(word, created, modified) "+
                    "values (?, ?, ?)", (word, creadate, moddate))
        else:
            self.execSql("update wikiwords set modified = ? where word = ?",
                    (moddate, word))
                    
        self.cachedWikiWords[word] = 1


    def setContent(self, word, text, moddate = None, creadate = None):
        """
        Store unicode text for wikiword word, regardless if previous
        content exists or not. creadate will be used for new content
        only.
        
        moddate -- Modification date to store or None for current
        creadate -- Creation date to store or None for current        
        """
        
        output = open(self.getWikiWordFileName(word), 'w')
        output.write(BOM_UTF8)
        output.write(utf8Enc(text)[0])
        output.close()
        
        self._updatePageEntry(word, moddate, creadate)


#     def getContentAndInfo(self, word):
#         """
#         Get content and further information about a word
#         """
#         content = self.getContent(word)


    def renameContent(self, oldWord, newWord):
        """
        The content which was stored under oldWord is stored
        after the call under newWord. The self.cachedWikiWords
        dictionary is updated, other caches won't be updated.
        """
        self.execSql("update wikiwords set word = ? where word = ?",
                (newWord, oldWord))

        rename(self.getWikiWordFileName(oldWord),
                self.getWikiWordFileName(newWord))
        del self.cachedWikiWords[oldWord]
        self.cachedWikiWords[newWord] = 1


    def deleteContent(self, word):
        self.execSql("delete from wikiwords where word = ?", (word,))
        if exists(self.getWikiWordFileName(word)):
            unlink(self.getWikiWordFileName(word))
        del self.cachedWikiWords[word]
        

    # ---------- Rebuilding the wiki ----------
    def rebuildWiki(self, progresshandler):
        """
        progresshandler -- Object, fulfilling the GuiProgressHandler
            protocol
        """
        # get all of the wikiWords
        wikiWords = self.getAllPageNamesFromDisk()   # Replace this call
                
        progresshandler.open(len(wikiWords) + 1)
        try:
            step = 1
    
            # re-save all of the pages
            self.clearCacheTables()
            for wikiWord in wikiWords:
                progresshandler.update(step, u"Rebuilding %s" % wikiWord)
                self._updatePageEntry(wikiWord)
                wikiPage = self.createPage(wikiWord)
                wikiPage.update(wikiPage.getContent(), False)  # TODO AGA processing
                step = step + 1
    
        finally:            
            progresshandler.close()

#         # get all of the wikiWords
#         wikiWords = self.getAllPageNamesFromDisk()   # Replace this call
#         # get the saved searches
#         titles = self.getSavedSearchTitles()
#         searches = [(title, self.getSearchDatablock(title)) for title in titles]
# #         searches = self.getSavedSearches()
# 
#         self.close()
#                 
#         progresshandler.open(len(wikiWords) + len(searches) + 1)
# 
#         try:
#             step = 1
#             # recreate the db
#             progresshandler.update(step, "Recreating database")
#             createWikiDB("", self.dataDir, True)
#             # reopen the wiki
#             self._reinit()
#             # re-save all of the pages
#             for wikiWord in wikiWords:
#                 progresshandler.update(step, u"Rebuilding %s" % wikiWord)
#                 wikiPage = self.createPage(wikiWord)
#                 self._updatePageEntry(wikiWord)
#                 wikiPage.update(wikiPage.getContent(), False)
#                 step += 1
# 
#             # resave searches
#             for title, datablock in searches:
#                 progresshandler.update(step, u"Reading search %s" % title)
#                 self.saveSearch(title, datablock)
# 
# ##             self.close()
# ##             self._reinit()
#             
#         finally:            
#             progresshandler.close()
#     
    
    
    # ---------- The rest ----------

    _CAPABILITIES = {
        "rebuild": 1
        }

    def checkCapability(self, capkey):
        """
        Check the capabilities of this WikiData implementation.
        The capkey names the capability, the function returns normally
        a version number or None if not supported
        """
        return WikiData._CAPABILITIES.get(capkey, None)


    def getPage(self, wikiWord):
        """
        Fetch a WikiPage for the wikiWord, throws WikiWordNotFoundException
        if word doesn't exist
        """
        if not self.isDefinedWikiWord(wikiWord):
            raise WikiWordNotFoundException, u"Word '%s' not in wiki" % wikiWord

        return WikiPage(self, wikiWord)

    def getPageNoError(self, wikiWord):
        """
        fetch a WikiPage for the wikiWord. If it doesn't exist, return
        one without throwing an error and without updating the cache
        """
        return WikiPage(self, wikiWord)

    def createPage(self, wikiWord):
        """
        create a new wikiPage for the wikiWord. Cache is not updated until
        page is saved
        """
#         ti = time()
#         self.execSql(
#                 "insert into wikiwords(word, created, modified) values (?, ?, ?)",
#                 (wikiWord, ti, ti))
#         self.cachedWikiWords[wikiWord] = 1
        return self.getPageNoError(wikiWord)

    def getChildRelationships(self, wikiWord, existingonly=False,
            selfreference=True):
        """
        get the child relations to this word
        existingonly -- List only existing wiki words
        selfreference -- List also wikiWord if it references itself
        """
        sql = "select relation from wikirelations where word = ?"
        children = self.execSqlQuerySingleColumn(sql, (wikiWord,))
        if not selfreference:
            try:
                children.remove(wikiWord)
            except ValueError:
                pass
        
        if existingonly:
            return filter(lambda w: self.cachedWikiWords.has_key(w), children)
        else:
            return children

    # TODO More efficient
    def _hasChildren(self, wikiWord, existingonly=False,
            selfreference=True):
        return len(self.getChildRelationships(wikiWord, existingonly,
                selfreference)) > 0
                
    # TODO More efficient                
    def getChildRelationshipsAndHasChildren(self, wikiWord, existingonly=False,
            selfreference=True):
        """
        get the child relations to this word as sequence of tuples
            (<child word>, <has child children?>). Used when expanding
            a node in the tree control.
        existingonly -- List only existing wiki words
        selfreference -- List also wikiWord if it references itself
        """
        children = self.getChildRelationships(wikiWord, existingonly,
                selfreference)
                
        return map(lambda c: (c, self._hasChildren(c, existingonly,
                selfreference)), children)


    def getParentRelationships(self, toWord):
        "get the parent relations to this word"
        return self.execSqlQuerySingleColumn(
                "select word from wikirelations where relation = ?", (toWord,))

    def addRelationship(self, word, toWord):
        """
        Add a relationship from word toWord. Returns True if relation added.
        A relation from one word to another is unique and can't be added twice.
        """
        data = self.execSqlQuery("select relation from wikirelations where "+
                "word = ? and relation = ?", (word, toWord))
        returnValue = False
        if len(data) < 1:
            self.execSql("insert into wikirelations(word, relation, created) "+
                    "values (?, ?, ?)", (word, toWord, time()))
            returnValue = True
        return returnValue

    def getAllAliases(self):
        # get all of the aliases
        return self.execSqlQuerySingleColumn("select value from wikiwordprops where key = 'alias'")

    def getAllRelations(self):
        "get all of the relations in the db"
        relations = []
        data = self.execSqlQuery("select word, relation from wikirelations")
        for row in data:
            relations.append((row[0], row[1]))
        return relations

    def getWikiWordsStartingWith(self, thisStr, includeAliases=False):
        "get the list of words starting with thisStr. used for autocompletion."
        words = self.getAllDefinedPageNames()
        if includeAliases:
            words.extend(self.getAllAliases())
        startingWith = [word for word in words if word.startswith(thisStr)]
        return startingWith

    def getWikiWordsWith(self, thisStr):
        "get the list of words with thisStr in them."
        return [word for word in self.getAllDefinedPageNames()
                if word.lower().find(thisStr) != -1]

    def getWikiWordsModifiedWithin(self, days):
        timeDiff = time()-(86400*days)
        rows = self.execSqlQuery("select word, modified from wikiwords")
        return [row[0] for row in rows if float(row[1]) >= timeDiff]


    def getParentLessWords(self):
        """
        get the words that have no parents. also returns nodes that have files but
        no entries in the wikiwords table.
        """
        words = self.getAllDefinedPageNames()
        relations = self.getAllRelations()
        rightSide = [relation for (word, relation) in relations]

        # get the list of wiki files
#         wikiFiles = [file.replace(".wiki", "") for file in listdir(self.dataDir)
#                      if file.endswith(".wiki")]
        wikiFiles = self.getAllPageNamesFromDisk()

        # append the words that don't exist in the words db
        words.extend([file for file in wikiFiles if file not in words])

        # find those that have no parent relations
        return [word for word in words if word not in rightSide]

    def renameWord(self, word, toWord):
        if WikiFormatting.isWikiWord(toWord):
            try:
                self.getPage(toWord)
                raise WikiDataException, u"Cannot rename '%s' to '%s', '%s' already exists" % (word, toWord, toWord)
            except WikiWordNotFoundException:
                pass

            # commit anything pending so we can rollback on error
            self.commit()

            try:
                # self.execSql("update wikiwords set word = ? where word = ?", (toWord, word))
                self.execSql("update wikirelations set word = ? where word = ?", (toWord, word))
                self.execSql("update wikirelations set relation = ? where relation = ?", (toWord, word))
                self.execSql("update wikiwordprops set word = ? where word = ?", (toWord, word))
                self.execSql("update todos set word = ? where word = ?", (toWord, word))
                self.renameContent(word, toWord)
                # rename(join(self.dataDir, "%s.wiki" % word), join(self.dataDir, "%s.wiki" % toWord))  # !!!
                self.commit()
#                 del self.cachedWikiWords[word]
#                 self.cachedWikiWords[toWord] = 1
            except:
                self.connWrap.rollback()
                raise

            # now i have to search the wiki files and replace the old word with the new
            searchOp = SearchAndReplace.SearchReplaceOperation()
            searchOp.wikiWide = True
            searchOp.wildCard = 'no'
            searchOp.caseSensitive = True
            searchOp.searchStr = word
            
            results = self.search(searchOp)
            for resultWord in results:
                content = self.getContent(resultWord)
                content = content.replace(word, toWord)
                self.setContent(resultWord, content)
                
#                 file = join(self.dataDir, "%s.wiki" % resultWord)
# 
#                 fp = open(file)
#                 lines = fp.readlines()
#                 fp.close()
# 
#                 bakFileName = "%s.bak" % file
#                 fp = open(bakFileName, 'w')
#                 for line in lines:
#                     fp.write(line.replace(word, toWord))
#                 fp.close()
# 
#                 unlink(file)
#                 rename(bakFileName, file)

        else:
            raise WikiDataException, u"'%s' is an invalid wiki word" % toWord

    def deleteWord(self, word):
        """
        delete everything about the wikiword passed in. an exception is raised
        if you try and delete the wiki root node.
        """
        if word != self.pWiki.wikiName:
            try:
                self.commit()
                # don't delete the relations to the word since other
                # pages still have valid outward links to this page.
                # just delete the content

                self.execSql("delete from wikirelations where word = ?", (word,))
                self.execSql("delete from wikiwordprops where word = ?", (word,))
                # self.execSql("delete from wikiwords where word = ?", (word,))
                self.execSql("delete from todos where word = ?", (word,))
                self.deleteContent(word)
#                 del self.cachedWikiWords[word]
#                 wikiFile = self.getWikiWordFileName(word)
#                 if exists(wikiFile):
#                     unlink(wikiFile)
                self.commit()

                # due to some bug we have to close and reopen the db sometimes
                self.connWrap.close()
                conn = gadfly.gadfly("wikidb", self.dataDir)
                self.connWrap = DbStructure.ConnectWrap(conn)

            except:
                self.connWrap.rollback()
                raise
        else:
            raise WikiDataException, "You cannot delete the root wiki node"

    def deleteChildRelationships(self, fromWord):
        self.execSql("delete from wikirelations where word = ?", (fromWord,))

    def setProperty(self, word, key, value):
        # make sure the value doesn't already exist for this property
        data = self.execSqlQuery("select word from wikiwordprops where "+
                "word = ? and key = ? and value = ?", (word, key, value))
        # if it doesn't insert it
        returnValue = False
        if len(data) < 1:
            self.execSql("insert into wikiwordprops(word, key, value) "+
                    "values (?, ?, ?)", (word, key, value))
            returnValue = True
        return returnValue

    def getPropertyNames(self):
        names = self.execSqlQuerySingleColumn("select distinct(key) from wikiwordprops order by key")
        return [name for name in names if not name.startswith('global.')]

    def getPropertyNamesStartingWith(self, startingWith):
        names = self.execSqlQuerySingleColumn("select distinct(key) from wikiwordprops order by key")
        return [name for name in names if name.startswith(startingWith)]

    def getGlobalProperties(self):
        if not self.cachedGlobalProps:
            data = self.execSqlQuery("select key, value from wikiwordprops order by key")
            globalMap = {}
            for (key, val) in data:
                if key.startswith('global.'):
                    globalMap[key] = val
            self.cachedGlobalProps = globalMap

        return self.cachedGlobalProps

    def getDistinctPropertyValues(self, key):
        return self.execSqlQuerySingleColumn("select distinct(value) "+
                "from wikiwordprops where key = ? order by value", (key,))

    def getWordsWithPropertyValue(self, key, value):
        words = []
        data = self.execSqlQuery("select word from wikiwordprops "+
                "where key = ? and value = ?", (key, value))
        for row in data:
            words.append(row[0])
        return words

    def getAliasesWikiWord(self, alias):
        """
        If alias is an alias for another word, return that,
        otherwise return alias itself
        """
        if not self.isAlias(alias):
            return alias

        aliases = self.getWordsWithPropertyValue("alias", alias)
        if len(aliases) > 0:
            return aliases[0]
        return alias # None

    def isAlias(self, word):
        "check if a word is an alias for another"
        if self.cachedWikiWords.has_key(word):
            return self.cachedWikiWords.get(word) == 2
        return False

    def addTodo(self, word, todo):
        self.execSql("insert into todos(word, todo) values (?, ?)", (word, todo))

    def getTodos(self):
        todos = []
        data = self.execSqlQuery("select word, todo from todos")
        for row in data:
            todos.append((row[0], row[1]))
        return todos

    def deleteProperties(self, word):
        self.execSql("delete from wikiwordprops where word = ?", (word,))

    def deleteTodos(self, word):
        self.execSql("delete from todos where word = ?", (word,))

    def findBestPathFromWordToWord(self, word, toWord):
        "finds the shortest path from word to toWord"
        bestPath = findShortestPath(self._assembleWordGraph(word, {}), word,
                toWord, [])
        if bestPath: bestPath.reverse()
        return bestPath

    def _assembleWordGraph(self, word, graph):
        """
        recursively builds a graph of each of words parent relations

        Not part of public API!
        """
        if not graph.has_key(word):
            parents = self.getParentRelationships(word)
            graph[word] = parents;
            for parent in parents:
                self._assembleWordGraph(parent, graph)
        return graph

    def getAllSubWords(self, word, includeRoot=False):
        """
        Return all words which are children, grandchildren, etc.
        of word. Used by the "export Sub-Tree" functions
        """

        subWords = []
        if (includeRoot):
            subWords.append(word)
        allWords = self.getAllDefinedPageNames()
        for allWordsItem in allWords:
            if allWordsItem != word and self.findBestPathFromWordToWord(allWordsItem, word):
                subWords.append(allWordsItem)
        return subWords

#     def search(self, forPattern, processAnds=True):
#         if processAnds:
#             andPatterns = [re.compile(pattern, re.IGNORECASE)
#                            for pattern in forPattern.lower().split(' and ')]
#         else:
#             andPatterns = [re.compile(forPattern.lower(), re.IGNORECASE)]
# 
#         results = []
#         for word in self.getAllDefinedPageNames():  #glob.glob(join(self.dataDir, '*.wiki')):
#             # print "search1", repr(word), repr(self.getWikiWordFileName(word))
#             fileContents = self.getContent(word)
# 
#             patternsMatched = 0
#             for pattern in andPatterns:
#                 if pattern.search(fileContents):
#                     patternsMatched = patternsMatched + 1
# 
#             if patternsMatched == len(andPatterns):
#                 results.append(word)
# 
#         return results


    def search(self, sarOp):
        results = []
        for word in self.getAllDefinedPageNames():  #glob.glob(join(self.dataDir, '*.wiki')):
            # print "search1", repr(word), repr(self.getWikiWordFileName(word))
            fileContents = self.getContent(word)
            
            if sarOp.testText(fileContents) == True:
                results.append(word)
                
        return results


# ----- Begin of save search API -----

    def saveSearch(self, title, datablock):
        test = self.connWrap.execSqlQuerySingleItem(
                "select title from search_views where title = ?",
                (title,))
                
        if test is not None:
            self.connWrap.execSql(
                    "update search_views set datablock = ? where "+\
                    "title = ?", (datablock, title))
        else:
            self.connWrap.execSql(
                    "insert into search_views(title, datablock) "+\
                    "values (?, ?)", (title, datablock))

    def getSavedSearchTitles(self):
        return self.connWrap.execSqlQuerySingleColumn(
                "select title from search_views order by title")

    def getSearchDatablock(self, title):
        return self.connWrap.execSqlQuerySingleItem(
                "select datablock from search_views where title = ?", (title,),
                strConv=False)

    def deleteSavedSearch(self, title):
        self.connWrap.execSql(
                "delete from search_views where title = ?", (title,))

#     def saveSearch(self, title, datablock):
#         "save a search into the search_views table"
#         searchOp = SearchAndReplace.SearchReplaceOperation()
#         searchOp.setPackedSettings(datablock)
#         search = searchOp.searchStr
# 
#         data = self.execSqlQuery('select search from search_views where '+
#                 'search = ?', (search,))
#         if len(data) < 1:
#             self.execSql('insert into search_views(search) values (?)', (search,))
# 
#     def getSavedSearchTitles(self):
#         return self.execSqlQuerySingleColumn('select search from search_views order by search')
# 
#     def getSearchDatablock(self, title):
#         searchOp = SearchAndReplace.SearchReplaceOperation()
#         searchOp.searchStr = title
#         searchOp.wikiWide = True
#         searchOp.booleanOp = True
#         searchOp.setTitle(title)
#         return searchOp.getPackedSettings()
# 
#     def deleteSavedSearch(self, title):
#         self.execSql('delete from search_views where search = ?', (title,))

# ----- End of faked save search API from WikidPadCompact -----



#     def saveSearch(self, search):
#         "save a search into the search_views table"
#         data = self.execSqlQuery('select search from search_views where '+
#                 'search = ?', (search,))
#         if len(data) < 1:
#             self.execSql('insert into search_views(search) values (?)', (search,))
# 
#     def getSavedSearches(self):
#         return self.execSqlQuerySingleColumn('select search from search_views order by search')
# 
#     def deleteSavedSearch(self, search):
#         self.execSql('delete from search_views where search = ?', (search,))


    def clearCacheTables(self):
        """
        Clear all tables in the database which contain non-essential
        (cache) information as well as other cache information.
        Needed before updating the whole wiki
        """
        DbStructure.recreateCacheTables(self.connWrap)
        self.connWrap.commit()

        self.cachedWikiWords = {}
        self.cachedGlobalProps = None



    def execSql(self, sql, params=None):
        "utility method, executes the sql, no return"
        return self.connWrap.execSql(sql, params)
#         cursor = self.dbConn.cursor()
#         if params:
#             params = tuple(map(_uniToUtf8, params))
#             cursor.execute(sql, params)
#         else:
#             cursor.execute(sql)
#         cursor.close()

    def execSqlQuery(self, sql, params=None):
        "utility method, executes the sql, returns query result"
        return self.connWrap.execSqlQuery(sql, params)
#         cursor = self.dbConn.cursor()
#         if params:
#             params = tuple(map(_uniToUtf8, params))
#             cursor.execute(sql, params)
#         else:
#             cursor.execute(sql)
#         data = cursor.fetchall()
#         cursor.close()
#         data = map(lambda row: map(_utf8ToUni, row), data)
#         return data

    def execSqlQuerySingleColumn(self, sql, params=None):
        "utility method, executes the sql, returns query result"
        return self.connWrap.execSqlQuerySingleColumn(sql, params)
#         data = self.execSqlQuery(sql, params)
#         return [row[0] for row in data]

    def commit(self):
        self.connWrap.commit()

    def close(self):
        self.commit()
        self.connWrap.close()


class WikiPage:
    """
    holds the data for a wikipage. fetched via the WikiData.getPage method.
    """
    def __init__(self, wikiData, wikiWord):
        self.wikiData = wikiData
        self.wikiWord = wikiWord
        self.wikiFile = self.wikiData.getWikiWordFileName(self.wikiWord)
        self.parentRelations = None
        # self.childRelations = None
        self.todos = None
        self.props = None
        self.modified, self.created = None, None

#         # load the wiki word info from the db
#         if not toload or 'info' in toload:
#             self.getWikiWordInfo()
# 
#         # load the wiki word parents
#         if not toload or 'parents' in toload:
#             self.getParentRelationships()
# 
#         # fetch the props of the wiki word
#         if not toload or 'props' in toload:
#             self.getProperties()
# 
#         # fetch the todo list
#         if not toload or 'todos' in toload:
#             self.getTodos()

        # does this page need to be saved
        self.saveDirty = False
        self.updateDirty = False

        # save when this page was last saved
        self.lastSave = time()

        # save when this page was last saved
        self.lastUpdate = time()


    def getWikiWord(self):
        return self.wikiWord


    def getWikiWordInfo(self):
        if self.modified is None:
            dates = self.wikiData.execSqlQuery("select modified, created "+
                    "from wikiwords where word = ?", (self.wikiWord,))
            if len(dates) > 0:
                self.modified, self.created = dates[0]
            else:
                ti = time()
                self.modified, self.created = ti, ti  # ?

        return self.modified, self.created

    def getParentRelationships(self):
        if self.parentRelations is None:
            self.parentRelations = \
                    self.wikiData.getParentRelationships(self.wikiWord)
        
        return self.parentRelations

        
    def getChildRelationships(self, existingonly=False, selfreference=True):
        """
        Does not support caching
        """
        return self.wikiData.getChildRelationships(self.wikiWord,
                existingonly, selfreference)


    def getChildRelationshipsAndHasChildren(self, existingonly=False,
            selfreference=True):
        """
        Does not support caching
        """
        return self.wikiData.getChildRelationshipsAndHasChildren(self.wikiWord,
                existingonly, selfreference)

    def getProperties(self):
        if self.props is None:
            data = self.wikiData.execSqlQuery("select key, value "+
                    "from wikiwordprops where word = ?", (self.wikiWord,))
            self.props = {}
            for (key, val) in data:
                self.addProperty(key, val)
                
        return self.props


    def getPropertyOrGlobal(self, propkey, default=None):
        """
        Tries to find a property on this page and returns the first value.
        If it can't be found for page, it is searched for a global
        property with this name. If this also can't be found,
        default (normally None) is returned.
        """
        props = self.getProperties()
        if props.has_key(propkey):
            return props[propkey][0]
        else:
            globalProps = self.wikiData.getGlobalProperties()     
            return globalProps.get(u"global."+propkey, default)


    def addProperty(self, key, val):
        values = self.props.get(key)
        if not values:
            values = []
            self.props[key] = values
        values.append(val)
        

    def getTodos(self):
        if self.todos is None:
            self.todos = self.wikiData.execSqlQuerySingleColumn("select todo from todos "+
                    "where word = ?", (self.wikiWord,))
                    
        return self.todos
        
    def getNonAliasPage(self):
        """
        If this page belongs to an alias of a wiki word, return a page for
        the real one, otherwise return self
        """
        if not self.wikiData.isAlias(self.wikiWord):
            return self
        
        word = self.wikiData.getAliasesWikiWord(self.wikiWord)
        return WikiPage(self.wikiData, word)


    def getContent(self):
        return self.wikiData.getContent(self.wikiWord)


    def save(self, text, alertPWiki=True):
        """
        Saves the content of current wiki page.
        """
        self.lastSave = time()
        self.wikiData.setContent(self.wikiWord, text)
        self.saveDirty = False


    def update(self, text, alertPWiki=True):
        """
        Update additional cached informations (properties, todos, relations)
        """
        self.deleteChildRelationships()
        self.deleteProperties()
        self.deleteTodos()

        footnotesAsWikiwords = self.wikiData.pWiki.configuration.getboolean(
                "main", "footnotes_as_wikiwords")
        
        formatMap = WikiFormatting.getExpressionsFormatList(
                WikiFormatting.UpdateExpressions,
                self.wikiData.pWiki.wikiWordsEnabled,
                footnotesAsWikiwords)

        tokens = self.wikiData._updateTokenizer.tokenize(text, sync=True)

        if len(tokens) >= 2:
            tok = tokens[0]

            for nexttok in tokens[1:]:
                stindex = tok[1]
                if stindex == -1:
                    styleno = WikiFormatting.FormatTypes.Default
                else:
                    styleno = formatMap[stindex]

                if styleno == WikiFormatting.FormatTypes.ToDo:
                    self.addTodo(tok[2]["todoContent"])
#                 elif styleno == WikiFormatting.FormatTypes.WikiWord2:
#                     self.addChildRelationship(
#                             WikiFormatting.normalizeWikiWord(
#                             text[tok[0]:nexttok[0]]))
                elif styleno == WikiFormatting.FormatTypes.WikiWord:
                    self.addChildRelationship(
                            WikiFormatting.normalizeWikiWord(
                            text[tok[0]:nexttok[0]], footnotesAsWikiwords))
                elif styleno == WikiFormatting.FormatTypes.Property:
                    propName = tok[2]["propertyName"]
                    propValue = tok[2]["propertyValue"]

                    if propName == "alias":
                        word = WikiFormatting.normalizeWikiWord(propValue,
                                footnotesAsWikiwords)
                        if word is not None:
                            self.wikiData.cachedWikiWords[word] = 2
                            self.setProperty("alias", word)
#                         if not WikiFormatting.WikiWordRE.match(word):
#                             word = u"[%s]" % word
#                         self.wikiData.cachedWikiWords[word] = 2
#                         self.setProperty("alias", word)
                    else:
                        self.setProperty(propName, propValue)
                        
                tok = nexttok

        # update the modified time
#         self.modified = time()
#         self.wikiData.execSql("update wikiwords set modified = ? where word = ?",
#                 (self.modified, self.wikiWord))
        self.lastUpdate = time()   # self.modified

        # kill the global prop cache in case any props were added
        self.wikiData.cachedGlobalProps = None

        # add a relationship to the scratchpad at the root
        if self.wikiWord == self.wikiData.pWiki.wikiName:
            self.addChildRelationship(u"ScratchPad")

        # clear the dirty flag
        self.updateDirty = False

        if alertPWiki:
            self.wikiData.pWiki.informWikiPageUpdate(self)

    def addChildRelationship(self, toWord):
        self.wikiData.addRelationship(self.wikiWord, toWord)
        
    def setProperty(self, key, value):
        self.wikiData.setProperty(self.wikiWord, key, value)
        self.props = None        
        
    def addTodo(self, todo):
        if todo not in self.getTodos():
            self.wikiData.addTodo(self.wikiWord, todo)
            self.todos.append(todo)            


    def deleteChildRelationships(self):
        self.wikiData.deleteChildRelationships(self.wikiWord)
        self.childRelations = []

    def deleteProperties(self):
        self.wikiData.deleteProperties(self.wikiWord)
        self.props = {}

    def deleteTodos(self):
        self.wikiData.deleteTodos(self.wikiWord)
        self.todos = []

    def setDirty(self, dirt):
        self.saveDirty = dirt
        self.updateDirty = dirt

    def getDirty(self):
        return (self.saveDirty, self.updateDirty)


####################################################
# module level functions
####################################################

# def createWikiDB(wikiName, dataDir, overwrite=False):
#     "creates the initial db"
#     if (not exists(dataDir) or overwrite):
#         if (not exists(dataDir)):
#             mkdir(dataDir)
# 
#         # create the new gadfly database
#         connection = gadfly.gadfly()
#         connection.startup("wikidb", dataDir)
# 
#         # create the tables, etc
#         cursor = connection.cursor()
#         cursor.execute("create table wikiwords (word varchar, created varchar, modified varchar)")
#         cursor.execute("create table wikirelations (word varchar, relation varchar, created varchar)")
#         cursor.execute("create table wikiwordprops (word varchar, key varchar, value varchar)")
#         cursor.execute("create table todos (word varchar, todo varchar)")
#         cursor.execute("create table search_views (search varchar)")
# 
#         cursor.execute("create unique index wikiwords_pkey on wikiwords(word)")
#         cursor.execute("create unique index wikirelations_pkey on wikirelations(word, relation)")
#         cursor.execute("create index wikirelations_word on wikirelations(word)")
#         cursor.execute("create index wikiwordprops_word on wikiwordprops(word)")
# 
#         connection.commit()
# 
#         # close the connection
#         connection.close()
# 
#     else:
#         raise WikiDBExistsException, u"database already exists at location: %s" % dataDir



def findShortestPath(graph, start, end, path):   # path=[]
    "finds the shortest path in the graph from start to end"
    path = path + [start]
    if start == end:
        return path
    if not graph.has_key(start):
        return None
    shortest = None
    for node in graph[start]:
        if node not in path:
            newpath = findShortestPath(graph, node, end, path)
            if newpath:
                if not shortest or len(newpath) < len(shortest):
                    shortest = newpath

    return shortest
