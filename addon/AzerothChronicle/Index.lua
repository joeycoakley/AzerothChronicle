--[[
Azeroth Chronicle - Index.lua

Derives browsable views from the raw event journal, in memory, on demand.

This reads the captured journal and never writes to it. Everything here is
recomputed from events, so it can be thrown away and rebuilt at any time,
exactly like the companion's SQLite tables. The difference is that this
version includes the session in progress, because it reads memory rather
than a file the client has not written yet.

No language model is involved. Everything the journal pane shows about
quests, characters, and places is computed here from text the player
actually saw.
]]

local ADDON_NAME = ...

AC = AC or {}
AC.Index = {}

local cache
local cachedEventCount = -1

local DIALOGUE_EVENT_TYPES = {
    CHAT_MSG_MONSTER_SAY = "says",
    CHAT_MSG_MONSTER_YELL = "yells",
    CHAT_MSG_MONSTER_EMOTE = "emote",
    CHAT_MSG_MONSTER_WHISPER = "whispers",
}

-- Matches the companion's rule deliberately: the creature id inside the
-- GUID, not the whole GUID, which encodes one particular spawn. Two
-- sightings of the same character must collapse to one entry.
local function NpcKeyFor(source)
    if type(source) ~= "table" then return nil end

    local guid = source.guid
    if type(guid) == "string" and guid ~= "" then
        local creatureId = guid:match("^%a+%-%d+%-%d+%-%d+%-%d+%-(%d+)%-")
        if creatureId then
            return "creature:" .. creatureId
        end
        return "guid:" .. guid
    end

    if source.name then
        return "name:" .. source.name
    end
    return nil
end

AC.Index.NpcKeyFor = NpcKeyFor

local function EnsureQuest(quests, order, questId)
    local quest = quests[questId]
    if not quest then
        quest = {
            id = questId,
            dialogue = {},
            npcKeys = {},
        }
        quests[questId] = quest
        order[#order + 1] = questId
    end
    return quest
end

local function EnsureNpc(npcs, order, key)
    local npc = npcs[key]
    if not npc then
        npc = {
            key = key,
            encounters = 0,
            questsGiven = {},
            questsTurnedIn = {},
            dialogue = {},
            zones = {},
        }
        npcs[key] = npc
        order[#order + 1] = key
    end
    return npc
end

local function EnsureZone(zones, order, name)
    local zone = zones[name]
    if not zone then
        zone = { name = name, eventCount = 0, questIds = {}, npcKeys = {} }
        zones[name] = zone
        order[#order + 1] = name
    end
    return zone
end

local function KeepEarliest(target, field, value)
    if value == nil then return end
    if target[field] == nil or value < target[field] then
        target[field] = value
    end
end

local function KeepLatest(target, field, value)
    if value == nil then return end
    if target[field] == nil or value > target[field] then
        target[field] = value
    end
end

local function KeepIfPresent(target, field, value)
    if value ~= nil and value ~= "" then
        target[field] = value
    end
end

-- Relevance is a ranking over things the player actually did, not a guess.
-- Giving a quest outweighs talking, talking outweighs standing nearby, and
-- every contribution produces a line of evidence so the pane can show why
-- someone ranks where they do instead of asserting a number.
local WEIGHT_QUEST_GIVEN = 10
local WEIGHT_QUEST_TURNED_IN = 8
local WEIGHT_PROGRESS_DIALOGUE = 3
local WEIGHT_GREETING = 2
local WEIGHT_GOSSIP = 1

local function CountKeys(t)
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    return n
end

local function ScoreNpc(npc, newestTimestamp)
    local given = CountKeys(npc.questsGiven)
    local turnedIn = CountKeys(npc.questsTurnedIn)

    local score = given * WEIGHT_QUEST_GIVEN
        + turnedIn * WEIGHT_QUEST_TURNED_IN
        + (npc.progressCount or 0) * WEIGHT_PROGRESS_DIALOGUE
        + (npc.greetingCount or 0) * WEIGHT_GREETING
        + (npc.gossipCount or 0) * WEIGHT_GOSSIP

    -- A gentle recency nudge so someone met an hour ago edges out an equal
    -- acquaintance from weeks back. Deliberately small: what a character
    -- did with you matters more than when.
    if npc.lastSeenAt and newestTimestamp and newestTimestamp > 0 then
        local ageDays = (newestTimestamp - npc.lastSeenAt) / 86400
        if ageDays < 1 then
            score = score + 3
        elseif ageDays < 7 then
            score = score + 1
        end
    end

    local evidence = {}
    if given > 0 then
        evidence[#evidence + 1] = (given == 1)
            and "gave you a quest"
            or ("gave you " .. given .. " quests")
    end
    if turnedIn > 0 then
        evidence[#evidence + 1] = (turnedIn == 1)
            and "you completed a quest for them"
            or ("you completed " .. turnedIn .. " quests for them")
    end
    if (npc.gossipCount or 0) > 0 or (npc.greetingCount or 0) > 0 then
        local spoke = (npc.gossipCount or 0) + (npc.greetingCount or 0)
        evidence[#evidence + 1] = "spoke with you " .. spoke
            .. (spoke == 1 and " time" or " times")
    end
    if npc.firstZone then
        evidence[#evidence + 1] = "met in " .. npc.firstZone
    end

    npc.score = score
    npc.evidence = evidence
    npc.questsGivenCount = given
    npc.questsTurnedInCount = turnedIn
end

local function AddDialogue(list, entry)
    list[#list + 1] = entry
end

local function Build()
    local db = AzerothChronicleDB
    if not db or not db.events then
        return {
            quests = {}, questOrder = {},
            npcs = {}, npcOrder = {},
            zones = {}, zoneOrder = {},
            timeline = {}, levelUps = {}, eventCount = 0,
        }
    end

    local quests, questOrder = {}, {}
    local npcs, npcOrder = {}, {}
    local zones, zoneOrder = {}, {}
    local timeline, levelUps = {}, {}
    local newestTimestamp = 0

    for _, event in ipairs(db.events) do
        local eventType = event.type
        local timestamp = event.timestamp
        local location = event.location or {}
        local source = event.source
        local npcKey = NpcKeyFor(source)

        if timestamp and timestamp > newestTimestamp then
            newestTimestamp = timestamp
        end

        local npc
        if npcKey then
            npc = EnsureNpc(npcs, npcOrder, npcKey)
            npc.encounters = npc.encounters + 1
            KeepIfPresent(npc, "name", source and source.name)
            KeepEarliest(npc, "firstSeenAt", timestamp)
            KeepLatest(npc, "lastSeenAt", timestamp)
            if location.zone then
                npc.zones[location.zone] = true
                if not npc.firstZone then npc.firstZone = location.zone end
            end
        end

        if location.zone and location.zone ~= "" then
            local zone = EnsureZone(zones, zoneOrder, location.zone)
            zone.eventCount = zone.eventCount + 1
            KeepEarliest(zone, "firstSeenAt", timestamp)
            KeepLatest(zone, "lastSeenAt", timestamp)
            if location.mapId then zone.mapId = location.mapId end
            if npcKey then zone.npcKeys[npcKey] = true end
        end

        local questData = event.quest
        local questId = questData and questData.id
        local quest
        if type(questId) == "number" then
            quest = EnsureQuest(quests, questOrder, questId)
            KeepEarliest(quest, "firstSeenAt", timestamp)
            KeepIfPresent(quest, "title", questData.title)
            KeepIfPresent(quest, "zone", location.zone)
            if npcKey then quest.npcKeys[npcKey] = true end
            if location.zone then
                local zone = EnsureZone(zones, zoneOrder, location.zone)
                zone.questIds[questId] = true
            end
        end

        if eventType == "QUEST_DETAIL" and quest then
            KeepIfPresent(quest, "description", questData.description)
            KeepIfPresent(quest, "objectivesText", questData.objectivesText)
            if npcKey then
                KeepIfPresent(quest, "giverKey", npcKey)
                KeepIfPresent(quest, "giverName", source and source.name)
                npc.questsGiven[questId] = true
            end

        elseif eventType == "QUEST_ACCEPTED" and quest then
            KeepEarliest(quest, "acceptedAt", timestamp)
            if npcKey then
                KeepIfPresent(quest, "giverKey", npcKey)
                KeepIfPresent(quest, "giverName", source and source.name)
                npc.questsGiven[questId] = true
            end

        elseif eventType == "QUEST_PROGRESS" and quest then
            if questData.progressText then
                AddDialogue(quest.dialogue, {
                    kind = "progress", text = questData.progressText,
                    timestamp = timestamp, npcKey = npcKey,
                    npcName = source and source.name,
                })
                if npc then
                    npc.progressCount = (npc.progressCount or 0) + 1
                    AddDialogue(npc.dialogue, {
                        kind = "progress", text = questData.progressText,
                        timestamp = timestamp, questId = questId,
                    })
                end
            end

        elseif eventType == "QUEST_COMPLETE" and quest then
            KeepEarliest(quest, "completedAt", timestamp)
            KeepIfPresent(quest, "completionText", questData.completionText)
            if questData.completionText then
                AddDialogue(quest.dialogue, {
                    kind = "completion", text = questData.completionText,
                    timestamp = timestamp, npcKey = npcKey,
                    npcName = source and source.name,
                })
                if npc then
                    AddDialogue(npc.dialogue, {
                        kind = "completion", text = questData.completionText,
                        timestamp = timestamp, questId = questId,
                    })
                end
            end

        elseif eventType == "QUEST_TURNED_IN" and quest then
            KeepEarliest(quest, "turnedInAt", timestamp)
            KeepIfPresent(quest, "xpReward", questData.xpReward)
            KeepIfPresent(quest, "moneyReward", questData.moneyReward)
            if npc then npc.questsTurnedIn[questId] = true end

        elseif eventType == "QUEST_REMOVED" and quest then
            KeepEarliest(quest, "removedAt", timestamp)

        elseif eventType == "QUEST_GREETING" then
            local greeting = event.greeting or {}
            if npc then
                npc.greetingCount = (npc.greetingCount or 0) + 1
                if greeting.text then
                    AddDialogue(npc.dialogue, {
                        kind = "greeting", text = greeting.text, timestamp = timestamp,
                    })
                end
            end

        elseif eventType == "GOSSIP_SHOW" then
            local gossip = event.gossip or {}
            if npc then
                npc.gossipCount = (npc.gossipCount or 0) + 1
                if gossip.text then
                    AddDialogue(npc.dialogue, {
                        kind = "gossip", text = gossip.text, timestamp = timestamp,
                    })
                end
            end

        elseif eventType == "QUEST_LOG_SNAPSHOT" then
            local entries = (event.questLog or {}).entries or {}
            for _, entry in ipairs(entries) do
                if type(entry.questId) == "number" then
                    local carried = EnsureQuest(quests, questOrder, entry.questId)
                    KeepIfPresent(carried, "title", entry.title)
                    KeepEarliest(carried, "firstSeenAt", timestamp)
                    carried.knownFromSnapshot = true
                end
            end

        elseif eventType == "ZONE_DISCOVERED" then
            local discovery = event.discovery or {}
            if discovery.zone then
                local zone = EnsureZone(zones, zoneOrder, discovery.zone)
                KeepEarliest(zone, "discoveredAt", timestamp)
                if discovery.mapId then zone.mapId = discovery.mapId end
            end

        elseif eventType == "PLAYER_LEVEL_UP" then
            local progression = event.progression or {}
            levelUps[#levelUps + 1] = {
                level = progression.level,
                timestamp = timestamp,
                zone = location.zone,
            }

        elseif DIALOGUE_EVENT_TYPES[eventType] then
            local spoken = event.dialogue or {}
            if npc and spoken.text then
                AddDialogue(npc.dialogue, {
                    kind = DIALOGUE_EVENT_TYPES[eventType],
                    text = spoken.text, timestamp = timestamp,
                })
            end
        end

        timeline[#timeline + 1] = {
            type = eventType,
            timestamp = timestamp,
            zone = location.zone,
            questId = type(questId) == "number" and questId or nil,
            questTitle = questData and questData.title,
            npcKey = npcKey,
            npcName = source and source.name,
        }
    end

    -- A quest observed being accepted was never known only from a snapshot.
    for _, quest in pairs(quests) do
        if quest.acceptedAt then
            quest.knownFromSnapshot = false
        end

        if quest.turnedInAt then
            quest.state = "completed"
        elseif quest.removedAt then
            -- The client fires the same removal event on hand-in, so this
            -- only reads as abandoned when no turn-in was ever seen.
            quest.state = "abandoned"
        elseif quest.acceptedAt or quest.knownFromSnapshot then
            quest.state = "active"
        else
            quest.state = "seen"
        end
    end

    for _, npc in pairs(npcs) do
        ScoreNpc(npc, newestTimestamp)
    end

    return {
        quests = quests, questOrder = questOrder,
        npcs = npcs, npcOrder = npcOrder,
        zones = zones, zoneOrder = zoneOrder,
        timeline = timeline,
        levelUps = levelUps,
        eventCount = #db.events,
        newestTimestamp = newestTimestamp,
    }
end

-- Rebuilt whenever the journal has grown, so the pane always reflects the
-- session in progress rather than the last time it was opened.
function AC.Index.Get(forceRebuild)
    local db = AzerothChronicleDB
    local count = (db and db.events) and #db.events or 0

    if forceRebuild or not cache or count ~= cachedEventCount then
        cache = Build()
        cachedEventCount = count
    end
    return cache
end

function AC.Index.Invalidate()
    cache = nil
    cachedEventCount = -1
end

-- Sorted views. Sorting happens here rather than in the UI so the ordering
-- rules stay next to the data that justifies them.

function AC.Index.QuestsByRecency()
    local index = AC.Index.Get()
    local list = {}
    for _, id in ipairs(index.questOrder) do
        list[#list + 1] = index.quests[id]
    end
    table.sort(list, function(a, b)
        local aTime = a.turnedInAt or a.acceptedAt or a.firstSeenAt or 0
        local bTime = b.turnedInAt or b.acceptedAt or b.firstSeenAt or 0
        if aTime == bTime then
            return (a.id or 0) > (b.id or 0)
        end
        return aTime > bTime
    end)
    return list
end

function AC.Index.ActiveQuests()
    local list = {}
    for _, quest in ipairs(AC.Index.QuestsByRecency()) do
        if quest.state == "active" then
            list[#list + 1] = quest
        end
    end
    return list
end

function AC.Index.NpcsByRelevance()
    local index = AC.Index.Get()
    local list = {}
    for _, key in ipairs(index.npcOrder) do
        list[#list + 1] = index.npcs[key]
    end
    table.sort(list, function(a, b)
        if (a.score or 0) == (b.score or 0) then
            return (a.lastSeenAt or 0) > (b.lastSeenAt or 0)
        end
        return (a.score or 0) > (b.score or 0)
    end)
    return list
end

function AC.Index.ZonesByRecency()
    local index = AC.Index.Get()
    local list = {}
    for _, name in ipairs(index.zoneOrder) do
        list[#list + 1] = index.zones[name]
    end
    table.sort(list, function(a, b)
        return (a.lastSeenAt or 0) > (b.lastSeenAt or 0)
    end)
    return list
end

-- ============================================================
-- Search
--
-- Searches the captured text itself, not just titles. The question this
-- exists to answer is "where do I know that name from", and the answer is
-- usually buried in the middle of a quest description the player read
-- three weeks ago.
-- ============================================================

local SNIPPET_PADDING = 70

-- Returns the matched text with enough either side to be recognizable,
-- so a result explains itself rather than just asserting a hit.
local function Snippet(text, queryLower)
    if not text then return nil end

    local startPos = text:lower():find(queryLower, 1, true)
    if not startPos then return nil end

    local from = math.max(1, startPos - SNIPPET_PADDING)
    local to = math.min(#text, startPos + #queryLower + SNIPPET_PADDING)

    local snippet = text:sub(from, to)
    snippet = snippet:gsub("\n", " ")

    if from > 1 then snippet = "..." .. snippet end
    if to < #text then snippet = snippet .. "..." end

    return snippet
end

local function Contains(text, queryLower)
    return type(text) == "string" and text:lower():find(queryLower, 1, true) ~= nil
end

function AC.Index.Search(query)
    local results = { quests = {}, npcs = {}, zones = {}, dialogue = {}, total = 0 }

    if type(query) ~= "string" then return results end
    query = query:match("^%s*(.-)%s*$")
    if query == "" then return results end

    local queryLower = query:lower()
    local index = AC.Index.Get()

    for _, questId in ipairs(index.questOrder) do
        local quest = index.quests[questId]
        local field, snippet

        -- Title first so an exact name match explains itself simply, then
        -- the body text, which is where most recollection actually lives.
        if Contains(quest.title, queryLower) then
            field, snippet = "title", quest.title
        elseif Contains(quest.description, queryLower) then
            field, snippet = "description", Snippet(quest.description, queryLower)
        elseif Contains(quest.objectivesText, queryLower) then
            field, snippet = "objectives", Snippet(quest.objectivesText, queryLower)
        elseif Contains(quest.completionText, queryLower) then
            field, snippet = "completion", Snippet(quest.completionText, queryLower)
        end

        if field then
            results.quests[#results.quests + 1] = {
                quest = quest, field = field, snippet = snippet,
            }
        end
    end

    for _, npcKey in ipairs(index.npcOrder) do
        local npc = index.npcs[npcKey]
        if Contains(npc.name, queryLower) then
            results.npcs[#results.npcs + 1] = { npc = npc, field = "name" }
        else
            for _, entry in ipairs(npc.dialogue) do
                if Contains(entry.text, queryLower) then
                    results.dialogue[#results.dialogue + 1] = {
                        npc = npc,
                        kind = entry.kind,
                        timestamp = entry.timestamp,
                        snippet = Snippet(entry.text, queryLower),
                    }
                    break
                end
            end
        end
    end

    for _, zoneName in ipairs(index.zoneOrder) do
        if Contains(zoneName, queryLower) then
            results.zones[#results.zones + 1] = { zone = index.zones[zoneName] }
        end
    end

    results.total = #results.quests + #results.npcs + #results.zones + #results.dialogue
    return results
end

function AC.Index.RecentTimeline(limit)
    local index = AC.Index.Get()
    local list = {}
    local total = #index.timeline
    local first = math.max(1, total - (limit or 50) + 1)
    for i = total, first, -1 do
        list[#list + 1] = index.timeline[i]
    end
    return list
end
