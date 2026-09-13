--[[
Azeroth Chronicle - Threads.lua

Story threads the player groups by hand.

There is a deliberate line in this addon between two kinds of data:

  Observations  - what the client told us happened. Append-only, never
                  edited, stored in AzerothChronicleDB.events.

  Annotations   - what the player decided it meant. Editable, stored
                  separately in AzerothChronicleDB.threads.

Threads are annotations. They are authored, not captured, so they live
outside the event journal and are never mixed into it.

The client does not expose quest prerequisites. Questie ships a
hand-maintained database plus a directory of corrections precisely because
that information does not exist client-side, and for a new game no such
database will exist at all. Rather than guess at groupings and be
confidently wrong, the addon lets the player group quests themselves and
confines itself to pointing out things it actually observed.
]]

local ADDON_NAME = ...

AC = AC or {}
AC.Threads = {}

local function Store()
    AzerothChronicleDB = AzerothChronicleDB or {}
    AzerothChronicleDB.threads = AzerothChronicleDB.threads or {}
    return AzerothChronicleDB.threads
end

local function GenerateThreadId()
    local entropy = 0
    local ok, uptime = pcall(GetTime)
    if ok and uptime then
        entropy = math.floor((uptime % 1) * 0xFFFF)
    end
    return string.format("thread-%d-%04x", time(),
        (math.random(0, 0xFFFF) + entropy) % 0x10000)
end

function AC.Threads.Create(name)
    name = tostring(name or ""):match("^%s*(.-)%s*$")
    if name == "" then return nil end

    local threads = Store()
    local id = GenerateThreadId()

    threads[id] = {
        id = id,
        name = name,
        createdAt = time(),
        updatedAt = time(),
        questIds = {},
    }
    return id
end

function AC.Threads.Get(threadId)
    return Store()[threadId]
end

function AC.Threads.Rename(threadId, name)
    local thread = Store()[threadId]
    if not thread then return false end

    name = tostring(name or ""):match("^%s*(.-)%s*$")
    if name == "" then return false end

    thread.name = name
    thread.updatedAt = time()
    return true
end

-- Deletes the grouping, never the history. Every quest, character and line
-- of dialogue in the thread stays exactly where it was, because none of it
-- belonged to the thread in the first place.
function AC.Threads.Delete(threadId)
    local threads = Store()
    if not threads[threadId] then return false end
    threads[threadId] = nil
    return true
end

function AC.Threads.AddQuest(threadId, questId)
    local thread = Store()[threadId]
    if not thread or type(questId) ~= "number" then return false end
    if thread.questIds[questId] then return false end

    thread.questIds[questId] = time()
    thread.updatedAt = time()
    return true
end

function AC.Threads.RemoveQuest(threadId, questId)
    local thread = Store()[threadId]
    if not thread or not thread.questIds[questId] then return false end

    thread.questIds[questId] = nil
    thread.updatedAt = time()
    return true
end

function AC.Threads.Contains(threadId, questId)
    local thread = Store()[threadId]
    return thread and thread.questIds[questId] ~= nil or false
end

-- Every thread a given quest belongs to. A quest can sit in more than one,
-- because stories overlap and forcing a single parent would make the player
-- choose where something "really" belongs.
function AC.Threads.ForQuest(questId)
    local matches = {}
    for _, thread in pairs(Store()) do
        if thread.questIds[questId] then
            matches[#matches + 1] = thread
        end
    end
    table.sort(matches, function(a, b) return (a.name or "") < (b.name or "") end)
    return matches
end

function AC.Threads.All()
    local list = {}
    for _, thread in pairs(Store()) do
        list[#list + 1] = thread
    end
    table.sort(list, function(a, b)
        return (a.updatedAt or 0) > (b.updatedAt or 0)
    end)
    return list
end

function AC.Threads.QuestIds(threadId)
    local thread = Store()[threadId]
    if not thread then return {} end

    local ids = {}
    for questId in pairs(thread.questIds) do
        ids[#ids + 1] = questId
    end
    table.sort(ids, function(a, b)
        return (thread.questIds[a] or 0) < (thread.questIds[b] or 0)
    end)
    return ids
end

function AC.Threads.Count()
    local n = 0
    for _ in pairs(Store()) do n = n + 1 end
    return n
end
