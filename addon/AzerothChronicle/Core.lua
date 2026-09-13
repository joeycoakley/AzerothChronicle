--[[
Azeroth Chronicle - Core.lua
V0 Capture Harness

Per docs/development-spec.md section 3.1: this proves the target client
exposes enough narrative information to reconstruct a player's journey.
Everything lives in one file for now; refactor into Capture/, Util/, UI/
only after capture behavior is proven against a real client (section 5).

Design principles in play here (see spec section 2):
  - Capture first, interpret later: store raw fields, do not summarize.
  - Raw SavedVariables data is canonical and append-only.
  - Never let a capture failure break quest interaction (section 27).

Interface number in AzerothChronicle.toc was read directly from this
client's .build.info (wow_classic_era 1.15.9 -> interface 11509). If this
addon is later loaded against WoW Forever, re-validate every event name
and API call below using /ac discovery on (section 25) before trusting
captured data.
]]

local ADDON_NAME = ...

AC = AC or {}
AC.API = {}
AC.Debug = {}
AC.state = { unregisteredEvents = {} }

local SCHEMA_VERSION = 1
local ADDON_VERSION = "0.0.1"

local sessionId
local sequenceCounter = 0

-- ============================================================
-- Debug / discovery mode (spec section 25)
-- ============================================================

AC.Debug.enabled = false
AC.Debug.discovery = false

function AC.Debug.Print(...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[#parts + 1] = tostring((select(i, ...)))
    end
    print("|cff33ccffAC|r " .. table.concat(parts, " "))
end

function AC.Debug.Log(...)
    if AC.Debug.enabled then
        AC.Debug.Print(...)
    end
end

-- Logs a candidate event firing and whatever fields we managed to pull
-- from it. This is the primary tool for validating Forever's API surface
-- against what worked in Classic Era.
function AC.Debug.Discover(eventName, fields)
    if not AC.Debug.discovery then return end
    AC.Debug.Print("[Discovery]", eventName)
    if type(fields) == "table" then
        for k, v in pairs(fields) do
            AC.Debug.Print("  " .. tostring(k) .. " = " .. tostring(v))
        end
    end
end

function AC.Debug.Error(context, err)
    AC.Debug.Print("|cffff5555ERROR|r", context, tostring(err))
end

-- Every capture handler goes through here so a broken capture path can
-- never interrupt gameplay (spec section 27).
local function SafeCall(context, fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then
        AC.Debug.Error(context, err)
    end
end

-- ============================================================
-- Identity / ids (spec sections 12-13)
-- ============================================================

local function GenerateSessionId()
    -- Do not call math.randomseed here: WoW's Lua sandbox does not expose
    -- it, and the client seeds math.random itself. For extra entropy, mix
    -- in the sub-second component of GetTime(), which is an uptime counter
    -- and so differs on every launch. That keeps two sessions started in
    -- the same wall-clock second from colliding.
    local entropy = 0
    local ok, uptime = pcall(GetTime)
    if ok and uptime then
        entropy = math.floor((uptime % 1) * 0xFFFF)
    end

    local datePart = date("!%Y%m%dT%H%M%SZ")
    local randPart = string.format("%04x", (math.random(0, 0xFFFF) + entropy) % 0x10000)
    return datePart .. "-" .. randPart
end

-- <CharacterGUID>:<SessionID>:<Sequence> - unique, no network dependency,
-- deterministic within a session. Never reuse across restarts.
local function GenerateEventId(characterGUID)
    sequenceCounter = sequenceCounter + 1
    return string.format("%s:%s:%06d", characterGUID or "Unknown", sessionId or "nosession", sequenceCounter)
end

-- Small non-cryptographic hash used only for gossip dedup (section 8),
-- not for security purposes.
local function HashString(str)
    if not str then return "0" end
    local hash = 5381
    for i = 1, #str do
        hash = (hash * 33 + string.byte(str, i)) % 4294967296
    end
    return string.format("%08x", hash)
end

local function GetCharacterIdentity()
    local name = UnitName("player") or "Unknown"
    local realm = GetRealmName() or "Unknown"
    local guid = UnitGUID("player") or ("Unknown-" .. name)
    local _, classToken = UnitClass("player")
    local level = UnitLevel("player") or 0
    return {
        guid = guid,
        name = name,
        realm = realm,
        class = classToken,
        level = level,
    }
end

-- ============================================================
-- API compatibility layer (spec section 26)
--
-- Wrap every client API call here rather than scattering direct calls
-- through capture logic, since Forever may expose a different API
-- generation than Classic Era. Each wrapper tries the modern API first,
-- falls back to the legacy global, and returns nil rather than throwing
-- when neither is available.
-- ============================================================

function AC.API.GetGossipText()
    if C_GossipInfo and C_GossipInfo.GetText then
        local ok, text = pcall(C_GossipInfo.GetText)
        if ok then return text end
    end
    if GetGossipText then
        local ok, text = pcall(GetGossipText)
        if ok then return text end
    end
    return nil
end

function AC.API.GetGossipOptions()
    if C_GossipInfo and C_GossipInfo.GetOptions then
        local ok, options = pcall(C_GossipInfo.GetOptions)
        if ok and options then return options end
    end
    if GetGossipOptions then
        local ok, packed = pcall(function() return { GetGossipOptions() } end)
        if ok and packed and #packed > 0 then
            local options = {}
            for i = 1, #packed, 2 do
                options[#options + 1] = { name = packed[i], type = packed[i + 1] }
            end
            return options
        end
    end
    return nil
end

function AC.API.GetGossipActiveQuests()
    if C_GossipInfo and C_GossipInfo.GetActiveQuests then
        local ok, quests = pcall(C_GossipInfo.GetActiveQuests)
        if ok then return quests end
    end
    if GetGossipActiveQuests then
        local ok, packed = pcall(function() return { GetGossipActiveQuests() } end)
        if ok and packed and #packed > 0 then
            local quests = {}
            for i = 1, #packed, 3 do
                quests[#quests + 1] = { title = packed[i], level = packed[i + 1], isComplete = packed[i + 2] }
            end
            return quests
        end
    end
    return nil
end

function AC.API.GetGossipAvailableQuests()
    if C_GossipInfo and C_GossipInfo.GetAvailableQuests then
        local ok, quests = pcall(C_GossipInfo.GetAvailableQuests)
        if ok then return quests end
    end
    if GetGossipAvailableQuests then
        local ok, packed = pcall(function() return { GetGossipAvailableQuests() } end)
        if ok and packed and #packed > 0 then
            local quests = {}
            for i = 1, #packed, 2 do
                quests[#quests + 1] = { title = packed[i], level = packed[i + 1] }
            end
            return quests
        end
    end
    return nil
end

function AC.API.GetQuestId()
    if GetQuestID then
        local ok, id = pcall(GetQuestID)
        if ok and id and id ~= 0 then return id end
    end
    return nil
end

function AC.API.GetQuestTitle()
    if GetTitleText then
        local ok, t = pcall(GetTitleText)
        if ok then return t end
    end
    return nil
end

function AC.API.GetQuestDescription()
    if GetQuestText then
        local ok, t = pcall(GetQuestText)
        if ok then return t end
    end
    return nil
end

function AC.API.GetQuestObjectives()
    if GetObjectiveText then
        local ok, t = pcall(GetObjectiveText)
        if ok then return t end
    end
    return nil
end

function AC.API.GetQuestProgressText()
    if GetProgressText then
        local ok, t = pcall(GetProgressText)
        if ok then return t end
    end
    return nil
end

function AC.API.GetQuestRewardText()
    if GetRewardText then
        local ok, t = pcall(GetRewardText)
        if ok then return t end
    end
    return nil
end

function AC.API.GetUnitIdentity(unit)
    local name, guid
    local ok1, n = pcall(UnitName, unit)
    if ok1 then name = n end
    local ok2, g = pcall(UnitGUID, unit)
    if ok2 then guid = g end
    return name, guid
end

function AC.API.GetLocation()
    local zone = "Unknown"
    local ok1, z = pcall(GetZoneText)
    if ok1 and z and z ~= "" then zone = z end

    -- Subzone is often the narratively meaningful place name: "Shadowglen"
    -- rather than "Teldrassil". Empty outdoors in some areas, so optional.
    local subZone
    local ok2, sz = pcall(GetSubZoneText)
    if ok2 and sz and sz ~= "" then subZone = sz end

    local mapId, x, y

    if C_Map then
        -- The correct call is GetBestMapForUnit. An earlier version of this
        -- file called C_Map.GetBestMapID, which does not exist, so the
        -- guard below skipped it and every event was written with no map id
        -- and no coordinates. Nothing errored. Prefer the real name and
        -- keep the other only as a defensive fallback for Forever.
        local getBestMap = C_Map.GetBestMapForUnit or C_Map.GetBestMapID
        if getBestMap then
            local ok3, id = pcall(getBestMap, "player")
            if ok3 then mapId = id end
        end

        if mapId and C_Map.GetPlayerMapPosition then
            local ok4, pos = pcall(C_Map.GetPlayerMapPosition, mapId, "player")
            if ok4 and pos then
                local ok5, px, py = pcall(pos.GetXY, pos)
                if ok5 then x, y = px, py end
            end
        end
    end

    -- Legacy fallback for clients predating the C_Map namespace.
    if not x and GetPlayerMapPosition then
        local ok6, px, py = pcall(GetPlayerMapPosition, "player")
        if ok6 then x, y = px, py end
    end

    return { zone = zone, subZone = subZone, mapId = mapId, x = x, y = y }
end

-- Which client APIs actually resolved on this build (spec section 25:
-- "show which APIs returned data").
--
-- This exists because a wrapper that calls a misspelled or removed API is
-- invisible: the existence guard skips it, no error is raised, and the
-- field is simply absent from every captured event. That is how map ids
-- and coordinates went missing through an entire validation session.
-- Probing by name turns a silent gap into a visible one.
-- Probes are split by kind, and the distinction is the whole point.
--
-- "required" is what this client is expected to provide; anything missing
-- there is a real gap that costs captured data. "fallback" entries are
-- older API generations the compat layer only reaches for when the modern
-- call is absent, so they are normally missing on a current client and
-- their absence means nothing.
--
-- Reporting both as plain failures would put a red line on every healthy
-- client, and a warning that always fires is a warning nobody reads.
local API_PROBES = {
    { "C_GossipInfo.GetText", function() return C_GossipInfo and C_GossipInfo.GetText end, "required" },
    { "C_GossipInfo.GetOptions", function() return C_GossipInfo and C_GossipInfo.GetOptions end, "required" },
    { "C_GossipInfo.GetActiveQuests", function() return C_GossipInfo and C_GossipInfo.GetActiveQuests end, "required" },
    { "C_GossipInfo.GetAvailableQuests", function() return C_GossipInfo and C_GossipInfo.GetAvailableQuests end, "required" },
    { "GetQuestID", function() return GetQuestID end, "required" },
    { "GetTitleText", function() return GetTitleText end, "required" },
    { "GetQuestText", function() return GetQuestText end, "required" },
    { "GetObjectiveText", function() return GetObjectiveText end, "required" },
    { "GetProgressText", function() return GetProgressText end, "required" },
    { "GetRewardText", function() return GetRewardText end, "required" },
    { "ItemTextGetText", function() return ItemTextGetText end, "required" },
    { "ItemTextGetItem", function() return ItemTextGetItem end, "required" },
    { "ItemTextGetPage", function() return ItemTextGetPage end, "required" },
    { "ItemTextGetCreator", function() return ItemTextGetCreator end, "required" },
    { "C_Map.GetBestMapForUnit", function() return C_Map and C_Map.GetBestMapForUnit end, "required" },
    { "C_Map.GetPlayerMapPosition", function() return C_Map and C_Map.GetPlayerMapPosition end, "required" },
    { "GetZoneText", function() return GetZoneText end, "required" },
    { "GetSubZoneText", function() return GetSubZoneText end, "required" },
    { "GetBuildInfo", function() return GetBuildInfo end, "required" },

    { "GetGossipText", function() return GetGossipText end, "fallback" },
    { "GetGossipOptions", function() return GetGossipOptions end, "fallback" },
    { "GetGossipActiveQuests", function() return GetGossipActiveQuests end, "fallback" },
    { "GetGossipAvailableQuests", function() return GetGossipAvailableQuests end, "fallback" },
    { "GetPlayerMapPosition", function() return GetPlayerMapPosition end, "fallback" },
    { "C_Map.GetBestMapID", function() return C_Map and C_Map.GetBestMapID end, "fallback" },
}

-- Returns four lists: required resolved/missing, then fallback
-- resolved/missing. Only missingRequired is cause for concern.
function AC.API.Probe()
    local resolved, missingRequired = {}, {}
    local fallbacksPresent, fallbacksAbsent = {}, {}

    for _, probe in ipairs(API_PROBES) do
        local name, getter, kind = probe[1], probe[2], probe[3]
        local ok, fn = pcall(getter)
        local present = ok and fn and true or false

        if kind == "fallback" then
            if present then
                fallbacksPresent[#fallbacksPresent + 1] = name
            else
                fallbacksAbsent[#fallbacksAbsent + 1] = name
            end
        elseif present then
            resolved[#resolved + 1] = name
        else
            missingRequired[#missingRequired + 1] = name
        end
    end

    return resolved, missingRequired, fallbacksPresent, fallbacksAbsent
end

-- ============================================================
-- SavedVariables / session lifecycle (spec sections 13-14)
-- ============================================================

local function InitDB()
    AzerothChronicleDB = AzerothChronicleDB or {}
    local db = AzerothChronicleDB

    db.schemaVersion = SCHEMA_VERSION

    db.metadata = db.metadata or { createdAt = time() }
    db.metadata.addonVersion = ADDON_VERSION

    db.character = GetCharacterIdentity()

    db.sessions = db.sessions or {}
    db.events = db.events or {}

    db.settings = db.settings or {
        captureWorldDialogue = true,
        captureGossip = true,
        captureItemText = true,
        debug = false,
        discovery = false,
    }

    AC.Debug.enabled = db.settings.debug and true or false
    AC.Debug.discovery = db.settings.discovery and true or false

    return db
end

local function StartSession()
    sessionId = GenerateSessionId()
    sequenceCounter = 0

    local clientVersion, clientBuild, _, interfaceVersion = GetBuildInfo()

    -- Record which required APIs were missing on this client build. This is
    -- permanent evidence in the canonical journal: if a later import shows
    -- gaps in the data, the session entry says whether the API was simply
    -- unavailable on that build rather than the capture logic being wrong.
    -- Absent legacy fallbacks are not recorded, since they are expected and
    -- would only add noise to every session on every current client.
    local _, missingApis = AC.API.Probe()

    local session = {
        id = sessionId,
        startedAt = time(),
        addonVersion = ADDON_VERSION,
        schemaVersion = SCHEMA_VERSION,
        clientVersion = clientVersion,
        clientBuild = clientBuild,
        interfaceVersion = interfaceVersion,
        missingApis = (#missingApis > 0) and missingApis or nil,
    }

    AzerothChronicleDB.sessions[sessionId] = session
    return session
end

-- Every capture path funnels through here to append one event to the
-- canonical, append-only journal (spec section 11).
local function AppendEvent(eventType, fields)
    local db = AzerothChronicleDB
    if not db then return nil end

    local character = db.character
    local eventId = GenerateEventId(character.guid)
    local location = AC.API.GetLocation()

    local event = {
        id = eventId,
        schemaVersion = SCHEMA_VERSION,
        sessionId = sessionId,
        character = {
            guid = character.guid,
            name = character.name,
            realm = character.realm,
            class = character.class,
            level = UnitLevel("player") or character.level,
        },
        type = eventType,
        timestamp = time(),
        location = location,
    }

    for k, v in pairs(fields or {}) do
        event[k] = v
    end

    db.events[#db.events + 1] = event
    AC.Debug.Log(eventType, "captured. id:", eventId)

    return event
end

-- ============================================================
-- Quest lifecycle capture (spec section 7)
-- ============================================================

-- Best-effort "who am I currently talking to" tracked across the quest
-- lifecycle, since not every quest event exposes a unit token.
local lastQuestGiverName, lastQuestGiverGUID

local function CaptureQuestDetail()
    local questId = AC.API.GetQuestId()
    local title = AC.API.GetQuestTitle()
    local description = AC.API.GetQuestDescription()
    local objectives = AC.API.GetQuestObjectives()

    local giverName, giverGUID = AC.API.GetUnitIdentity("questnpc")
    if not giverName then
        giverName, giverGUID = AC.API.GetUnitIdentity("npc")
    end
    lastQuestGiverName, lastQuestGiverGUID = giverName, giverGUID

    AC.Debug.Discover("QUEST_DETAIL", {
        questId = questId,
        title = title,
        giverName = giverName,
        hasDescription = description ~= nil,
        hasObjectives = objectives ~= nil,
    })

    AppendEvent("QUEST_DETAIL", {
        source = { name = giverName, guid = giverGUID },
        quest = {
            id = questId,
            title = title,
            description = description,
            objectivesText = objectives,
        },
    })
end

-- Signature confirmed on Classic Era 1.15.9 via discovery mode:
-- (questLogIndex, questID). Observed arg1=2, arg2=456.
--
-- Both raw args are still preserved. Forever may differ, and the failure
-- mode of guessing wrong is silent and expensive: a quest log index
-- imported as a quest id corrupts the story graph without any error.
-- rawArgs lets the companion detect a changed signature instead.
local function CaptureQuestAccepted(arg1, arg2)
    AC.Debug.Discover("QUEST_ACCEPTED", { arg1 = arg1, arg2 = arg2 })

    AppendEvent("QUEST_ACCEPTED", {
        source = { name = lastQuestGiverName, guid = lastQuestGiverGUID },
        quest = {
            id = arg2 or arg1,
            questLogIndex = arg1,
            rawArgs = { arg1, arg2 },
        },
    })
end

local function CaptureQuestProgress()
    local questId = AC.API.GetQuestId()
    local title = AC.API.GetQuestTitle()
    local progressText = AC.API.GetQuestProgressText()

    local giverName, giverGUID = AC.API.GetUnitIdentity("questnpc")
    if not giverName then
        giverName, giverGUID = lastQuestGiverName, lastQuestGiverGUID
    end

    AC.Debug.Discover("QUEST_PROGRESS", { questId = questId, hasProgressText = progressText ~= nil })

    AppendEvent("QUEST_PROGRESS", {
        source = { name = giverName, guid = giverGUID },
        quest = { id = questId, title = title, progressText = progressText },
    })
end

local function CaptureQuestComplete()
    local questId = AC.API.GetQuestId()
    local title = AC.API.GetQuestTitle()
    local rewardText = AC.API.GetQuestRewardText()

    local giverName, giverGUID = AC.API.GetUnitIdentity("questnpc")
    if not giverName then
        giverName, giverGUID = lastQuestGiverName, lastQuestGiverGUID
    end

    AC.Debug.Discover("QUEST_COMPLETE", { questId = questId, hasRewardText = rewardText ~= nil })

    AppendEvent("QUEST_COMPLETE", {
        source = { name = giverName, guid = giverGUID },
        quest = { id = questId, title = title, completionText = rewardText },
    })
end

local function CaptureQuestTurnedIn(questId, xpReward, moneyReward)
    AC.Debug.Discover("QUEST_TURNED_IN", { questId = questId, xpReward = xpReward, moneyReward = moneyReward })

    AppendEvent("QUEST_TURNED_IN", {
        quest = { id = questId, xpReward = xpReward, moneyReward = moneyReward },
    })
end

-- ============================================================
-- Gossip capture (spec section 8)
-- ============================================================

local lastGossipByKey = {}
local GOSSIP_DEDUPE_WINDOW_SECONDS = 30

local function CaptureGossip()
    local db = AzerothChronicleDB
    if not db.settings.captureGossip then return end

    local text = AC.API.GetGossipText()
    local npcName, npcGUID = AC.API.GetUnitIdentity("npc")
    local key = npcGUID or npcName or "unknown"

    local contentHash = HashString((text or "") .. "|" .. key)
    local now = time()
    local last = lastGossipByKey[key]
    if last and last.hash == contentHash and (now - last.at) < GOSSIP_DEDUPE_WINDOW_SECONDS then
        AC.Debug.Log("GOSSIP_SHOW deduped for", npcName)
        return
    end
    lastGossipByKey[key] = { hash = contentHash, at = now }

    local options = AC.API.GetGossipOptions()
    local activeQuests = AC.API.GetGossipActiveQuests()
    local availableQuests = AC.API.GetGossipAvailableQuests()

    AC.Debug.Discover("GOSSIP_SHOW", {
        npcName = npcName,
        hasText = text ~= nil,
        optionCount = options and #options or 0,
    })

    AppendEvent("GOSSIP_SHOW", {
        source = { name = npcName, guid = npcGUID },
        gossip = {
            text = text,
            contentHash = contentHash,
            options = options,
            activeQuests = activeQuests,
            availableQuests = availableQuests,
        },
    })
end

-- ============================================================
-- Item / book text capture (spec section 9)
-- ============================================================

local function CaptureItemText()
    local db = AzerothChronicleDB
    if not db.settings.captureItemText then return end

    local text
    if ItemTextGetText then
        local ok, t = pcall(ItemTextGetText)
        if ok then text = t end
    end

    local itemName
    if ItemTextGetItem then
        local ok, n = pcall(ItemTextGetItem)
        if ok then itemName = n end
    end

    -- Pages are preserved individually, never concatenated, so a
    -- multi-page book yields one event per page as the player turns pages.
    local page = 1
    if ItemTextGetPage then
        local ok, p = pcall(ItemTextGetPage)
        if ok and p then page = p end
    end

    local creator
    if ItemTextGetCreator then
        local ok, c = pcall(ItemTextGetCreator)
        if ok then creator = c end
    end

    AC.Debug.Discover("ITEM_TEXT_READY", { itemName = itemName, page = page, hasText = text ~= nil })

    AppendEvent("ITEM_TEXT_READY", {
        item = { name = itemName, page = page, text = text, creator = creator },
    })
end

-- ============================================================
-- World dialogue capture (spec section 10)
-- ============================================================

local function CaptureWorldDialogue(eventName, message, sender, guid)
    local db = AzerothChronicleDB
    if not db.settings.captureWorldDialogue then return end

    AC.Debug.Discover(eventName, { sender = sender, hasMessage = message ~= nil })

    AppendEvent(eventName, {
        source = { name = sender, guid = guid },
        dialogue = { text = message },
    })
end

-- ============================================================
-- Event registration
-- ============================================================

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")

local QUEST_EVENTS = {
    "QUEST_DETAIL",
    "QUEST_ACCEPTED",
    "QUEST_PROGRESS",
    "QUEST_COMPLETE",
    "QUEST_TURNED_IN",
}

local DIALOGUE_EVENTS = {
    "CHAT_MSG_MONSTER_SAY",
    "CHAT_MSG_MONSTER_YELL",
    "CHAT_MSG_MONSTER_EMOTE",
    "CHAT_MSG_MONSTER_WHISPER",
}

-- Register each event under its own guard.
--
-- RegisterEvent throws on an event name the client does not know. A single
-- loop would then abort partway through and silently skip every event
-- after the bad one, so an unrecognized QUEST_TURNED_IN on some future
-- client would also cost all the world dialogue events that follow it in
-- the list. Failures are collected and surfaced instead of ending capture.
local function RegisterCaptureEvents()
    local failed = {}

    local function tryRegister(evt)
        local ok = pcall(frame.RegisterEvent, frame, evt)
        if not ok then
            failed[#failed + 1] = evt
            AC.Debug.Error("RegisterEvent", "client rejected event " .. tostring(evt))
        end
    end

    tryRegister("GOSSIP_SHOW")
    tryRegister("ITEM_TEXT_READY")
    for _, evt in ipairs(QUEST_EVENTS) do
        tryRegister(evt)
    end
    for _, evt in ipairs(DIALOGUE_EVENTS) do
        tryRegister(evt)
    end

    -- Record on the session so a later import can tell "this client never
    -- fired that event" apart from "the player never did that thing".
    local session = AzerothChronicleDB.sessions and AzerothChronicleDB.sessions[sessionId]
    if session and #failed > 0 then
        session.unregisteredEvents = failed
    end

    AC.state.unregisteredEvents = failed
    return failed
end

local function OnAddonLoaded(loadedAddonName)
    if loadedAddonName ~= ADDON_NAME then return end

    -- Each phase is isolated so one failure cannot silently disable
    -- capture for the whole session. Event registration in particular must
    -- still happen even if session setup hit an unavailable API, since a
    -- missing session id degrades event identity but does not prevent
    -- recording narrative content.
    SafeCall("InitDB", InitDB)
    SafeCall("StartSession", StartSession)
    SafeCall("RegisterCaptureEvents", RegisterCaptureEvents)

    AC.Debug.Print("loaded. Type /ac status for info.")
end

frame:SetScript("OnEvent", function(_, event, ...)
    if event == "ADDON_LOADED" then
        SafeCall("ADDON_LOADED", OnAddonLoaded, ...)
    elseif event == "QUEST_DETAIL" then
        SafeCall("QUEST_DETAIL", CaptureQuestDetail)
    elseif event == "QUEST_ACCEPTED" then
        local arg1, arg2 = ...
        SafeCall("QUEST_ACCEPTED", CaptureQuestAccepted, arg1, arg2)
    elseif event == "QUEST_PROGRESS" then
        SafeCall("QUEST_PROGRESS", CaptureQuestProgress)
    elseif event == "QUEST_COMPLETE" then
        SafeCall("QUEST_COMPLETE", CaptureQuestComplete)
    elseif event == "QUEST_TURNED_IN" then
        local questId, xpReward, moneyReward = ...
        SafeCall("QUEST_TURNED_IN", CaptureQuestTurnedIn, questId, xpReward, moneyReward)
    elseif event == "GOSSIP_SHOW" then
        SafeCall("GOSSIP_SHOW", CaptureGossip)
    elseif event == "ITEM_TEXT_READY" then
        SafeCall("ITEM_TEXT_READY", CaptureItemText)
    elseif event == "CHAT_MSG_MONSTER_SAY" or event == "CHAT_MSG_MONSTER_YELL"
        or event == "CHAT_MSG_MONSTER_EMOTE" or event == "CHAT_MSG_MONSTER_WHISPER" then
        local message, sender = ...
        -- arg position 12 is the commonly-documented sender GUID slot for
        -- CHAT_MSG_* events; confirm with /ac discovery on before relying
        -- on it for anything beyond best-effort NPC identity.
        local guid = select(12, ...)
        SafeCall(event, CaptureWorldDialogue, event, message, sender, guid)
    end
end)

-- ============================================================
-- Slash commands (spec section 23)
-- ============================================================

local function CountEventsThisSession()
    local count = 0
    for _, evt in ipairs(AzerothChronicleDB.events) do
        if evt.sessionId == sessionId then
            count = count + 1
        end
    end
    return count
end

local function PrintStatus()
    local db = AzerothChronicleDB
    AC.Debug.Print("Azeroth Chronicle")
    AC.Debug.Print("Character: " .. db.character.name .. " - " .. db.character.realm)
    AC.Debug.Print("Session: " .. tostring(sessionId))
    AC.Debug.Print("Events this session: " .. CountEventsThisSession())
    AC.Debug.Print("Total events recorded: " .. #db.events)
    AC.Debug.Print("Debug: " .. tostring(db.settings.debug) .. "  Discovery: " .. tostring(db.settings.discovery))
end

-- Report every registered event type, including the ones sitting at zero.
--
-- Summarizing only a couple of quest stages hid exactly the thing being
-- validated: whether QUEST_COMPLETE fired. A stage that is missing has to
-- be visible as a zero, because an absent line reads as "fine" while a
-- zero reads as "not captured yet", and only one of those is true.
local function PrintStats()
    local counts = {}
    for _, evt in ipairs(AzerothChronicleDB.events) do
        counts[evt.type] = (counts[evt.type] or 0) + 1
    end

    AC.Debug.Print("Quest lifecycle:")
    for _, evt in ipairs(QUEST_EVENTS) do
        AC.Debug.Print(string.format("  %-18s %d", evt, counts[evt] or 0))
    end

    AC.Debug.Print("Gossip entries: " .. (counts["GOSSIP_SHOW"] or 0))
    AC.Debug.Print("Item/book text entries: " .. (counts["ITEM_TEXT_READY"] or 0))

    local dialogueCount = 0
    for _, evt in ipairs(DIALOGUE_EVENTS) do
        dialogueCount = dialogueCount + (counts[evt] or 0)
    end
    AC.Debug.Print("World dialogue: " .. dialogueCount)
    AC.Debug.Print("Total events recorded: " .. #AzerothChronicleDB.events)
end

local function PrintApis()
    local resolved, missingRequired, fallbacksPresent, fallbacksAbsent = AC.API.Probe()
    local requiredTotal = #resolved + #missingRequired

    AC.Debug.Print("API probe on build " .. tostring(select(2, GetBuildInfo())))

    if #missingRequired == 0 then
        AC.Debug.Print("|cff55ff55required|r: " .. #resolved .. " of " .. requiredTotal
            .. " resolved, nothing missing.")
    else
        AC.Debug.Print("|cffff5555required|r: " .. #resolved .. " of " .. requiredTotal
            .. " resolved. Missing:")
        for _, name in ipairs(missingRequired) do
            AC.Debug.Print("  |cffff5555" .. name .. "|r")
        end
    end

    -- Absent legacy fallbacks are the normal case, so they are reported in
    -- muted grey as information rather than as a problem.
    AC.Debug.Print("|cff999999legacy fallbacks: " .. #fallbacksPresent .. " of "
        .. (#fallbacksPresent + #fallbacksAbsent)
        .. " present. Absent is normal on a current client.|r")
    if #fallbacksPresent > 0 then
        for _, name in ipairs(fallbacksPresent) do
            AC.Debug.Print("|cff999999  available: " .. name .. "|r")
        end
    end

    local unregistered = AC.state.unregisteredEvents or {}
    if #unregistered > 0 then
        AC.Debug.Print("|cffff5555events the client rejected|r:")
        for _, evt in ipairs(unregistered) do
            AC.Debug.Print("  " .. evt)
        end
    else
        AC.Debug.Print("all capture events registered.")
    end

    -- Show what location capture actually produces right now, since a
    -- resolved API can still return nil in some places (indoors, instances).
    local loc = AC.API.GetLocation()
    AC.Debug.Print(string.format(
        "location now: zone=%s subZone=%s mapId=%s x=%s y=%s",
        tostring(loc.zone), tostring(loc.subZone), tostring(loc.mapId),
        loc.x and string.format("%.3f", loc.x) or "nil",
        loc.y and string.format("%.3f", loc.y) or "nil"))
end

local function PrintLast()
    local db = AzerothChronicleDB
    local last = db.events[#db.events]
    if not last then
        AC.Debug.Print("No events recorded yet.")
        return
    end
    AC.Debug.Print("Last event: " .. last.type .. " @ " .. date("%H:%M:%S", last.timestamp))
    AC.Debug.Print("  id: " .. last.id)
    if last.quest then
        AC.Debug.Print("  quest: " .. tostring(last.quest.title or last.quest.id))
    end
    if last.source and last.source.name then
        AC.Debug.Print("  source: " .. last.source.name)
    end
end

SLASH_AZEROTHCHRONICLE1 = "/ac"
SlashCmdList["AZEROTHCHRONICLE"] = function(msg)
    local db = AzerothChronicleDB
    if not db then
        print("Azeroth Chronicle is not initialized yet.")
        return
    end

    msg = (msg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
    local command, arg = msg:match("^(%S*)%s*(.-)$")

    if command == "" or command == "status" then
        PrintStatus()
    elseif command == "stats" then
        PrintStats()
    elseif command == "last" then
        PrintLast()
    elseif command == "apis" then
        PrintApis()
    elseif command == "debug" then
        if arg == "on" then
            db.settings.debug = true
            AC.Debug.enabled = true
            AC.Debug.Print("Debug logging enabled.")
        elseif arg == "off" then
            db.settings.debug = false
            AC.Debug.enabled = false
            AC.Debug.Print("Debug logging disabled.")
        else
            AC.Debug.Print("Usage: /ac debug on|off")
        end
    elseif command == "discovery" then
        if arg == "on" then
            db.settings.discovery = true
            AC.Debug.discovery = true
            AC.Debug.Print("Discovery mode enabled. Candidate event payloads will be logged.")
        elseif arg == "off" then
            db.settings.discovery = false
            AC.Debug.discovery = false
            AC.Debug.Print("Discovery mode disabled.")
        else
            AC.Debug.Print("Usage: /ac discovery on|off")
        end
    else
        AC.Debug.Print("Commands: /ac status, /ac stats, /ac last, /ac apis, /ac debug on|off, /ac discovery on|off")
    end
end
