--[[
Azeroth Chronicle - UI/Journal.lua

The in-game journal pane.

Reads the in-memory index, so it always includes the session in progress
rather than whatever was last written to disk. No language model, no
network, no waiting for a reload.

Two rules this file holds to:

  1. Never replace captured text with a paraphrase. Lists are navigation;
     the original wording is always one click away, because the wording is
     the thing the player slowed down to read.

  2. Show the evidence for any ranking. "Gave you two quests, met in
     Shadowglen" is honest in a way that a relevance score is not.

Blizzard templates only. Custom art is explicitly a later problem.
]]

local ADDON_NAME = ...

AC = AC or {}
AC.Journal = {}

local FRAME_WIDTH, FRAME_HEIGHT = 760, 500
local NAV_WIDTH = 150
local ROW_HEIGHT = 34

local frame, contentScroll, contentChild, detailText, headerText, breadcrumb, searchBox, actionButton
local RenderQuestDetail, RenderNpcDetail, RenderZoneDetail
local RenderThreadDetail, RenderThreadPicker
local rowPool = {}
local navButtons = {}
local pendingThreadQuestId
local currentView = "journey"
local detailStack = {}

local VIEWS = {
    { key = "journey", label = "Journey" },
    { key = "quests", label = "Quests" },
    { key = "characters", label = "Characters" },
    { key = "places", label = "Places" },
    { key = "threads", label = "Threads" },
    { key = "search", label = "Search" },
}

-- ============================================================
-- Small helpers
-- ============================================================

local function FormatTimestamp(timestamp)
    if not timestamp then return "" end
    return date("%d %b, %H:%M", timestamp)
end

local function FormatMoney(copper)
    if not copper or copper == 0 then return nil end
    local gold = math.floor(copper / 10000)
    local silver = math.floor((copper % 10000) / 100)
    local remainder = copper % 100
    local parts = {}
    if gold > 0 then parts[#parts + 1] = gold .. "g" end
    if silver > 0 then parts[#parts + 1] = silver .. "s" end
    if remainder > 0 then parts[#parts + 1] = remainder .. "c" end
    return table.concat(parts, " ")
end

local STATE_LABEL = {
    completed = "|cff55ff55completed|r",
    active = "|cffffcc00in progress|r",
    abandoned = "|cffff8888abandoned|r",
    seen = "|cff999999seen|r",
}

local function NpcDisplayName(npc)
    if not npc then return "Unknown" end
    return npc.name or npc.key or "Unknown"
end

-- ============================================================
-- Row pool
-- ============================================================

local function AcquireRow(index)
    local row = rowPool[index]
    if row then
        row:Show()
        return row
    end

    row = CreateFrame("Button", nil, contentChild)
    row:SetHeight(ROW_HEIGHT)
    row:SetPoint("LEFT", contentChild, "LEFT", 0, 0)
    row:SetPoint("RIGHT", contentChild, "RIGHT", 0, 0)

    local highlight = row:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetAllPoints(row)
    highlight:SetColorTexture(1, 1, 1, 0.08)

    -- Rows are a fixed height, so neither line may wrap. A search snippet
    -- is long by nature and would otherwise spill over the row beneath it.
    row.title = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    row.title:SetPoint("TOPLEFT", row, "TOPLEFT", 4, -3)
    row.title:SetPoint("RIGHT", row, "RIGHT", -4, 0)
    row.title:SetJustifyH("LEFT")
    row.title:SetWordWrap(false)

    row.detail = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.detail:SetPoint("TOPLEFT", row.title, "BOTTOMLEFT", 0, -2)
    row.detail:SetPoint("RIGHT", row, "RIGHT", -4, 0)
    row.detail:SetJustifyH("LEFT")
    row.detail:SetWordWrap(false)

    rowPool[index] = row
    return row
end

local function ReleaseRowsFrom(index)
    local i = index
    while rowPool[i] do
        rowPool[i]:Hide()
        i = i + 1
    end
end

local function LayoutRows(count)
    local offset = 0
    for i = 1, count do
        local row = rowPool[i]
        row:SetPoint("TOPLEFT", contentChild, "TOPLEFT", 0, -offset)
        offset = offset + ROW_HEIGHT
    end
    contentChild:SetHeight(math.max(offset, 1))
    ReleaseRowsFrom(count + 1)
end

local function HideActionButton()
    if actionButton then actionButton:Hide() end
end

local function ShowActionButton(label, onClick)
    if not actionButton then return end
    actionButton:SetText(label)
    actionButton:SetScript("OnClick", onClick)
    actionButton:Show()
end

local function ShowListMode()
    HideActionButton()
    detailText:Hide()
    detailText:SetText("")
end

local function ShowDetailMode(text)
    HideActionButton()
    ReleaseRowsFrom(1)
    detailText:SetText(text or "")
    detailText:Show()
    -- The font string reports its own wrapped height, which is the only
    -- reliable way to size a scroll child around variable-length quest text.
    contentChild:SetHeight(math.max(detailText:GetStringHeight() + 20, 1))
    contentScroll:SetVerticalScroll(0)
end

-- ============================================================
-- Views
-- ============================================================

local Render = {}

local function SetHeader(text, crumb)
    headerText:SetText(text)
    breadcrumb:SetText(crumb or "")
end

local function PushDetail(renderFn)
    detailStack[#detailStack + 1] = renderFn
    renderFn()
end

local function GoBack()
    detailStack[#detailStack] = nil
    local previous = detailStack[#detailStack]
    if previous then
        previous()
    else
        AC.Journal.Show(currentView)
    end
end

-- Quest detail is the page that matters most: it is where the captured
-- wording lives. Everything is shown verbatim.
function RenderQuestDetail(questId)
    local index = AC.Index.Get()
    local quest = index.quests[questId]
    if not quest then return end

    ShowListMode()
    SetHeader(quest.title or ("Quest " .. questId), "Quests")

    local lines = {}
    local function add(text) lines[#lines + 1] = text end

    add("|cffffd100" .. (quest.title or ("Quest " .. questId)) .. "|r")
    add(STATE_LABEL[quest.state] or quest.state or "")

    if quest.giverName then
        add("Given by |cff99ccff" .. quest.giverName .. "|r")
    end
    if quest.zone then
        add("In " .. quest.zone)
    end

    if quest.knownFromSnapshot then
        add("|cff999999You were already carrying this quest when the Chronicle"
            .. " began recording, so its origin was never observed.|r")
    end

    if quest.description then
        add("\n|cffffd100What you were told|r\n" .. quest.description)
    end
    if quest.objectivesText then
        add("\n|cffffd100Your task|r\n" .. quest.objectivesText)
    end

    if #quest.dialogue > 0 then
        add("\n|cffffd100Along the way|r")
        for _, entry in ipairs(quest.dialogue) do
            local who = entry.npcName and (entry.npcName .. ": ") or ""
            add("\n|cff999999" .. FormatTimestamp(entry.timestamp) .. "|r\n"
                .. who .. entry.text)
        end
    end

    if quest.xpReward or quest.moneyReward then
        local reward = {}
        if quest.xpReward then reward[#reward + 1] = quest.xpReward .. " experience" end
        local money = FormatMoney(quest.moneyReward)
        if money then reward[#reward + 1] = money end
        add("\n|cffffd100Reward|r\n" .. table.concat(reward, ", "))
    end

    local threads = AC.Threads and AC.Threads.ForQuest(questId) or {}
    if #threads > 0 then
        local names = {}
        for _, thread in ipairs(threads) do names[#names + 1] = thread.name end
        add("\n|cffffd100Part of|r\n" .. table.concat(names, ", "))
    end

    -- Observations, not conclusions. Each line is something the client
    -- showed you, offered so you can decide whether it means anything.
    local observations = AC.Index.ObservationsForQuest(questId)
    if #observations > 0 then
        add("\n|cffffd100Things the Chronicle noticed|r")
        for _, observation in ipairs(observations) do
            add("  " .. observation.detail)
        end
        add("|cff999999These are observations, not conclusions. The client never"
            .. " says which quests belong to one story.|r")
    end

    ShowDetailMode(table.concat(lines, "\n"))

    ShowActionButton("Add to thread", function()
        detailStack = {}
        PushDetail(function() RenderThreadPicker(questId) end)
    end)
end

function RenderNpcDetail(npcKey)
    local index = AC.Index.Get()
    local npc = index.npcs[npcKey]
    if not npc then return end

    ShowListMode()
    SetHeader(NpcDisplayName(npc), "Characters")

    local lines = {}
    local function add(text) lines[#lines + 1] = text end

    add("|cffffd100" .. NpcDisplayName(npc) .. "|r")

    if #npc.evidence > 0 then
        add("|cff999999" .. table.concat(npc.evidence, ", ") .. "|r")
    end

    add("\nFirst met " .. FormatTimestamp(npc.firstSeenAt)
        .. (npc.firstZone and (" in " .. npc.firstZone) or ""))
    add("Last seen " .. FormatTimestamp(npc.lastSeenAt))

    if npc.questsGivenCount > 0 then
        add("\n|cffffd100Quests from them|r")
        for questId in pairs(npc.questsGiven) do
            local quest = index.quests[questId]
            add("  " .. ((quest and quest.title) or ("Quest " .. questId))
                .. "  " .. (STATE_LABEL[quest and quest.state] or ""))
        end
    end

    if #npc.dialogue > 0 then
        add("\n|cffffd100What they said to you|r")
        for _, entry in ipairs(npc.dialogue) do
            add("\n|cff999999" .. FormatTimestamp(entry.timestamp)
                .. "  (" .. entry.kind .. ")|r\n" .. entry.text)
        end
    else
        add("\n|cff999999You have not recorded anything they said.|r")
    end

    ShowDetailMode(table.concat(lines, "\n"))
end

function RenderZoneDetail(zoneName)
    local index = AC.Index.Get()
    local zone = index.zones[zoneName]
    if not zone then return end

    ShowListMode()
    SetHeader(zoneName, "Places")

    local lines = {}
    local function add(text) lines[#lines + 1] = text end

    add("|cffffd100" .. zoneName .. "|r")
    if zone.discoveredAt then
        add("First arrived " .. FormatTimestamp(zone.discoveredAt))
    end
    add("Last here " .. FormatTimestamp(zone.lastSeenAt))

    local questIds = {}
    for questId in pairs(zone.questIds) do questIds[#questIds + 1] = questId end
    table.sort(questIds)

    if #questIds > 0 then
        add("\n|cffffd100What you did here|r")
        for _, questId in ipairs(questIds) do
            local quest = index.quests[questId]
            add("  " .. ((quest and quest.title) or ("Quest " .. questId))
                .. "  " .. (STATE_LABEL[quest and quest.state] or ""))
        end
    end

    local npcNames = {}
    for npcKey in pairs(zone.npcKeys) do
        npcNames[#npcNames + 1] = NpcDisplayName(index.npcs[npcKey])
    end
    table.sort(npcNames)

    if #npcNames > 0 then
        add("\n|cffffd100Who you met here|r")
        for _, name in ipairs(npcNames) do
            add("  " .. name)
        end
    end

    ShowDetailMode(table.concat(lines, "\n"))
end

function Render.journey()
    ShowListMode()
    SetHeader("Your Journey")

    local index = AC.Index.Get()
    local active = AC.Index.ActiveQuests()
    local db = AzerothChronicleDB

    local count = 0
    local function addRow(title, detail, onClick)
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(title)
        row.detail:SetText(detail or "")
        row:SetScript("OnClick", onClick)
        row:EnableMouse(onClick ~= nil)
    end

    local character = db and db.character or {}
    local descriptor = {}
    if character.race then descriptor[#descriptor + 1] = character.race end
    if character.class then descriptor[#descriptor + 1] = character.class end

    addRow("|cffffd100" .. (character.name or "Unknown") .. "|r",
        table.concat(descriptor, " ") ..
        (character.level and ("  |  level " .. character.level) or ""))

    addRow("Where you are now",
        (#active > 0)
            and (#active .. " " .. (#active == 1 and "thread" or "threads") .. " still open")
            or "Nothing open. Every thread you started is resolved.")

    if #active > 0 then
        for _, quest in ipairs(active) do
            local detail = (quest.giverName and ("from " .. quest.giverName .. "  ") or "")
                .. (quest.zone or "")
            addRow("  " .. (quest.title or ("Quest " .. quest.id)), detail, function()
                detailStack = {}
                PushDetail(function() RenderQuestDetail(quest.id) end)
            end)
        end
    end

    if #index.levelUps > 0 then
        local latest = index.levelUps[#index.levelUps]
        addRow("Most recent milestone",
            "reached level " .. tostring(latest.level)
            .. (latest.zone and (" in " .. latest.zone) or "")
            .. "  |  " .. FormatTimestamp(latest.timestamp))
    end

    addRow("Recorded so far",
        #index.questOrder .. " quests, " .. #index.npcOrder .. " characters, "
        .. #index.zoneOrder .. " places, " .. index.eventCount .. " moments")

    LayoutRows(count)
end

function Render.quests()
    ShowListMode()
    SetHeader("Quests")

    local quests = AC.Index.QuestsByRecency()
    local count = 0

    for _, quest in ipairs(quests) do
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(quest.title or ("Quest " .. quest.id))

        local bits = { STATE_LABEL[quest.state] or "" }
        if quest.giverName then bits[#bits + 1] = "from " .. quest.giverName end
        if quest.zone then bits[#bits + 1] = quest.zone end
        row.detail:SetText(table.concat(bits, "  |  "))

        row:EnableMouse(true)
        row:SetScript("OnClick", function()
            detailStack = {}
            PushDetail(function() RenderQuestDetail(quest.id) end)
        end)
    end

    if count == 0 then
        count = 1
        local row = AcquireRow(1)
        row.title:SetText("No quests recorded yet")
        row.detail:SetText("Accept a quest and it will appear here.")
        row:SetScript("OnClick", nil)
    end

    LayoutRows(count)
end

function Render.characters()
    ShowListMode()
    SetHeader("Characters")

    local npcs = AC.Index.NpcsByRelevance()
    local count = 0

    for _, npc in ipairs(npcs) do
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(NpcDisplayName(npc))
        -- The evidence, not the score. A number would tell the player
        -- nothing they could check.
        row.detail:SetText(table.concat(npc.evidence, ", "))

        row:EnableMouse(true)
        row:SetScript("OnClick", function()
            detailStack = {}
            PushDetail(function() RenderNpcDetail(npc.key) end)
        end)
    end

    if count == 0 then
        count = 1
        local row = AcquireRow(1)
        row.title:SetText("Nobody recorded yet")
        row.detail:SetText("Speak to someone and they will appear here.")
        row:SetScript("OnClick", nil)
    end

    LayoutRows(count)
end

function Render.places()
    ShowListMode()
    SetHeader("Places")

    local zones = AC.Index.ZonesByRecency()
    local count = 0

    for _, zone in ipairs(zones) do
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(zone.name)

        local questCount = 0
        for _ in pairs(zone.questIds) do questCount = questCount + 1 end
        local npcCount = 0
        for _ in pairs(zone.npcKeys) do npcCount = npcCount + 1 end

        row.detail:SetText(questCount .. " quests, " .. npcCount .. " characters"
            .. (zone.discoveredAt and ("  |  first arrived " .. FormatTimestamp(zone.discoveredAt)) or ""))

        row:EnableMouse(true)
        row:SetScript("OnClick", function()
            detailStack = {}
            PushDetail(function() RenderZoneDetail(zone.name) end)
        end)
    end

    if count == 0 then
        count = 1
        local row = AcquireRow(1)
        row.title:SetText("Nowhere recorded yet")
        row.detail:SetText("Travel somewhere and it will appear here.")
        row:SetScript("OnClick", nil)
    end

    LayoutRows(count)
end

-- Thread views. Threads are the player's own groupings, so nothing here
-- ever adds a quest on its own; suggestions are offered and chosen.

function RenderThreadDetail(threadId)
    local thread = AC.Threads and AC.Threads.Get(threadId)
    if not thread then return end

    ShowListMode()
    SetHeader(thread.name, "Threads")
    HideActionButton()

    local index = AC.Index.Get()
    local count = 0
    local function addRow(title, detail, onClick)
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(title)
        row.detail:SetText(detail or "")
        row:SetScript("OnClick", onClick)
        row:EnableMouse(onClick ~= nil)
    end

    local questIds = AC.Threads.QuestIds(threadId)

    if #questIds == 0 then
        addRow("Nothing in this thread yet",
            "Open a quest and use Add to thread, or take a suggestion below.")
    end

    for _, questId in ipairs(questIds) do
        local quest = index.quests[questId]
        local title = (quest and quest.title) or ("Quest " .. questId)
        local detail = quest and (STATE_LABEL[quest.state] or "") or ""
        if quest and quest.giverName then
            detail = detail .. "  |  from " .. quest.giverName
        end
        addRow(title, detail, function()
            detailStack = {}
            PushDetail(function() RenderQuestDetail(questId) end)
        end)
    end

    local suggestions = AC.Index.SuggestionsForThread(threadId)
    if #suggestions > 0 then
        addRow("|cffffd100Suggested, because of something observed|r",
            "Nothing is added unless you choose it.")

        for _, suggestion in ipairs(suggestions) do
            local quest = suggestion.quest
            addRow("  + " .. (quest.title or ("Quest " .. quest.id)),
                suggestion.because,
                function()
                    AC.Threads.AddQuest(threadId, quest.id)
                    RenderThreadDetail(threadId)
                end)
        end
    end

    LayoutRows(count)
end

-- Shown when a quest is open and the player chooses Add to thread. Reuses
-- the row list rather than introducing a dropdown, so picking a thread
-- works the same way as everything else in the pane.
function RenderThreadPicker(questId)
    ShowListMode()
    HideActionButton()

    local index = AC.Index.Get()
    local quest = index.quests[questId]
    SetHeader("Add to thread", quest and quest.title or nil)

    local count = 0
    local function addRow(title, detail, onClick)
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(title)
        row.detail:SetText(detail or "")
        row:SetScript("OnClick", onClick)
        row:EnableMouse(onClick ~= nil)
    end

    addRow("|cff55ff55New thread...|r", "Name a story and start it with this quest.",
        function()
            pendingThreadQuestId = questId
            if StaticPopup_Show then
                StaticPopup_Show("AZEROTH_CHRONICLE_NEW_THREAD")
            else
                AC.Debug.Print("This client has no StaticPopup_Show; cannot name a thread.")
            end
        end)

    for _, thread in ipairs(AC.Threads.All()) do
        local already = AC.Threads.Contains(thread.id, questId)
        local memberCount = #AC.Threads.QuestIds(thread.id)
        addRow(thread.name,
            already and "already in this thread"
                or (memberCount .. (memberCount == 1 and " quest" or " quests")),
            (not already) and function()
                AC.Threads.AddQuest(thread.id, questId)
                detailStack = {}
                PushDetail(function() RenderQuestDetail(questId) end)
            end or nil)
    end

    LayoutRows(count)
end

function Render.threads()
    ShowListMode()
    SetHeader("Threads")
    HideActionButton()

    local count = 0
    local function addRow(title, detail, onClick)
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(title)
        row.detail:SetText(detail or "")
        row:SetScript("OnClick", onClick)
        row:EnableMouse(onClick ~= nil)
    end

    local threads = AC.Threads and AC.Threads.All() or {}

    if #threads == 0 then
        addRow("No threads yet",
            "The client never says which quests belong to one story, so you "
            .. "decide. Open a quest and use Add to thread.")
    end

    for _, thread in ipairs(threads) do
        local questIds = AC.Threads.QuestIds(thread.id)
        local active = 0
        local index = AC.Index.Get()
        for _, questId in ipairs(questIds) do
            local quest = index.quests[questId]
            if quest and quest.state == "active" then active = active + 1 end
        end

        -- Deliberately never says "finished". We cannot know whether a
        -- story has more chapters, and claiming closure would be a lie.
        local state = (active > 0)
            and ("|cffffcc00" .. active .. " still open|r")
            or "|cff999999quiet for now|r"

        addRow(thread.name,
            #questIds .. (#questIds == 1 and " quest" or " quests") .. "  |  " .. state,
            function()
                detailStack = {}
                PushDetail(function() RenderThreadDetail(thread.id) end)
            end)
    end

    LayoutRows(count)
end

function Render.search()
    ShowListMode()

    local query = searchBox and searchBox:GetText() or ""
    query = query:match("^%s*(.-)%s*$")

    if query == "" then
        SetHeader("Search")
        local row = AcquireRow(1)
        row.title:SetText("Type to search your history")
        row.detail:SetText(
            "Searches quest text, what characters said to you, names and places. "
            .. "Only what you have actually encountered.")
        row:SetScript("OnClick", nil)
        LayoutRows(1)
        return
    end

    local results = AC.Index.Search(query)
    SetHeader("Search", results.total
        .. (results.total == 1 and " result for " or " results for ") .. '"' .. query .. '"')

    local count = 0
    local function addRow(title, detail, onClick)
        count = count + 1
        local row = AcquireRow(count)
        row.title:SetText(title)
        row.detail:SetText(detail or "")
        row:SetScript("OnClick", onClick)
        row:EnableMouse(onClick ~= nil)
    end

    if results.total == 0 then
        addRow("Nothing found",
            "Your character has not encountered anything matching that yet.")
        LayoutRows(count)
        return
    end

    for _, hit in ipairs(results.npcs) do
        addRow("|cff99ccff" .. NpcDisplayName(hit.npc) .. "|r",
            table.concat(hit.npc.evidence, ", "),
            function()
                detailStack = {}
                PushDetail(function() RenderNpcDetail(hit.npc.key) end)
            end)
    end

    for _, hit in ipairs(results.quests) do
        addRow("|cffffd100" .. (hit.quest.title or ("Quest " .. hit.quest.id)) .. "|r",
            "in " .. hit.field .. ": " .. (hit.snippet or ""),
            function()
                detailStack = {}
                PushDetail(function() RenderQuestDetail(hit.quest.id) end)
            end)
    end

    for _, hit in ipairs(results.dialogue) do
        addRow(NpcDisplayName(hit.npc) .. " |cff999999(" .. hit.kind .. ")|r",
            hit.snippet or "",
            function()
                detailStack = {}
                PushDetail(function() RenderNpcDetail(hit.npc.key) end)
            end)
    end

    for _, hit in ipairs(results.zones) do
        addRow(hit.zone.name, "a place you have been", function()
            detailStack = {}
            PushDetail(function() RenderZoneDetail(hit.zone.name) end)
        end)
    end

    LayoutRows(count)
end

-- ============================================================
-- Frame construction
-- ============================================================

local function CreateMainFrame()
    -- Template availability has bitten this project once already, so fall
    -- back rather than failing to load if a template is absent.
    local created
    for _, template in ipairs({ "BasicFrameTemplateWithInset", "BasicFrameTemplate" }) do
        local ok, result = pcall(CreateFrame, "Frame", "AzerothChronicleJournalFrame",
            UIParent, template)
        if ok and result then
            created = result
            break
        end
    end
    if not created then
        created = CreateFrame("Frame", "AzerothChronicleJournalFrame", UIParent)
    end
    return created
end

local function BuildFrame()
    frame = CreateMainFrame()
    frame:SetSize(FRAME_WIDTH, FRAME_HEIGHT)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:SetFrameStrata("HIGH")
    frame:Hide()

    -- Closes with Escape like a normal Blizzard window.
    tinsert(UISpecialFrames, "AzerothChronicleJournalFrame")

    local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    title:SetPoint("TOP", frame, "TOP", 0, -6)
    title:SetText("Azeroth Chronicle")

    if not frame.CloseButton then
        local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
        close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -2, -2)
    end

    -- Left navigation.
    for i, view in ipairs(VIEWS) do
        local button = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        button:SetSize(NAV_WIDTH - 20, 26)
        button:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -34 - (i - 1) * 30)
        button:SetText(view.label)
        button:SetScript("OnClick", function()
            AC.Journal.Show(view.key)
        end)
        navButtons[view.key] = button
    end

    local back = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    back:SetSize(NAV_WIDTH - 20, 22)
    back:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 12, 14)
    back:SetText("Back")
    back:SetScript("OnClick", GoBack)

    -- Context action for whatever is open. Hidden by default, so a view
    -- that does not set one cannot inherit the previous view's button.
    actionButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    actionButton:SetSize(140, 22)
    actionButton:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -34, 14)
    actionButton:Hide()

    -- Always visible, not just on the search view: the question it answers
    -- ("where do I know that name from") arrives while reading something
    -- else, and hiding the box behind a tab would add a step to the one
    -- interaction the pane exists for.
    searchBox = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    searchBox:SetSize(190, 20)
    searchBox:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -34, -32)
    searchBox:SetAutoFocus(false)
    searchBox:SetMaxLetters(80)

    local searchLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    searchLabel:SetPoint("RIGHT", searchBox, "LEFT", -6, 0)
    searchLabel:SetText("Search")

    searchBox:SetScript("OnTextChanged", function()
        if currentView ~= "search" then
            AC.Journal.Show("search")
        else
            local ok, err = pcall(Render.search)
            if not ok and AC.Debug and AC.Debug.Error then
                AC.Debug.Error("Journal.search", err)
            end
        end
    end)
    searchBox:SetScript("OnEscapePressed", function(self)
        self:SetText("")
        self:ClearFocus()
        AC.Journal.Show("journey")
    end)
    searchBox:SetScript("OnEnterPressed", function(self) self:ClearFocus() end)

    headerText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    headerText:SetPoint("TOPLEFT", frame, "TOPLEFT", NAV_WIDTH + 12, -34)

    breadcrumb = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    breadcrumb:SetPoint("TOPLEFT", headerText, "BOTTOMLEFT", 0, -2)

    contentScroll = CreateFrame("ScrollFrame", "AzerothChronicleJournalScroll", frame,
        "UIPanelScrollFrameTemplate")
    contentScroll:SetPoint("TOPLEFT", frame, "TOPLEFT", NAV_WIDTH + 12, -74)
    contentScroll:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -34, 14)

    contentChild = CreateFrame("Frame", nil, contentScroll)
    contentChild:SetSize(FRAME_WIDTH - NAV_WIDTH - 60, 1)
    contentScroll:SetScrollChild(contentChild)

    detailText = contentChild:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    detailText:SetPoint("TOPLEFT", contentChild, "TOPLEFT", 2, -2)
    detailText:SetWidth(FRAME_WIDTH - NAV_WIDTH - 70)
    detailText:SetJustifyH("LEFT")
    detailText:SetJustifyV("TOP")
    detailText:SetSpacing(2)
    detailText:Hide()
end

-- ============================================================
-- Public entry points
-- ============================================================

-- Naming a thread needs a text prompt, and Blizzard's own popup is the
-- native way to ask for one rather than building a bespoke dialog.
--
-- Everything below is deliberately defensive, because the client hides Lua
-- errors unless the player has turned them on. A mistake in a popup handler
-- is invisible by default: the dialog closes, nothing happens, and there is
-- no way to tell a failed call from a silent no-op. So handlers report
-- through the addon's own channel, and nothing depends on a field name that
-- might differ between client branches.

local function SafeUI(context, fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then
        if AC.Debug and AC.Debug.Error then
            AC.Debug.Error(context, err)
        else
            print("Azeroth Chronicle error in " .. tostring(context) .. ": " .. tostring(err))
        end
    end
    return ok
end

-- The edit box has been reachable under different names across branches, so
-- try each rather than assuming one and failing quietly.
local function PopupEditBoxText(dialog)
    if not dialog then return "" end

    local box = dialog.editBox or dialog.EditBox
    if not box and dialog.GetName and dialog:GetName() then
        box = _G[dialog:GetName() .. "EditBox"]
    end
    if not box and dialog.GetEditBox then
        local ok, result = pcall(dialog.GetEditBox, dialog)
        if ok then box = result end
    end

    if not box or not box.GetText then return "" end
    return box:GetText() or ""
end

-- pendingThreadQuestId is declared with the other file locals so the picker,
-- which runs earlier in the file, assigns the same upvalue this reads.

local function CreateThreadFromPopup(dialog)
    local name = PopupEditBoxText(dialog):match("^%s*(.-)%s*$")

    if name == "" then
        AC.Debug.Print("A thread needs a name. Nothing was created.")
        return
    end

    local threadId = AC.Threads and AC.Threads.Create(name)
    if not threadId then
        AC.Debug.Print("Could not create the thread \"" .. name .. "\".")
        return
    end

    if pendingThreadQuestId then
        AC.Threads.AddQuest(threadId, pendingThreadQuestId)
    end
    pendingThreadQuestId = nil

    AC.Debug.Print("Thread created: " .. name)
    AC.Journal.ShowThread(threadId)
end

StaticPopupDialogs = StaticPopupDialogs or {}
StaticPopupDialogs["AZEROTH_CHRONICLE_NEW_THREAD"] = {
    text = "Name this story thread",
    button1 = ACCEPT or "Accept",
    button2 = CANCEL or "Cancel",
    hasEditBox = true,
    maxLetters = 60,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    preferredIndex = 3,

    OnShow = function(self)
        SafeUI("NewThread.OnShow", function()
            local box = self.editBox or self.EditBox
            if box then
                box:SetText("")
                box:SetFocus()
            end
        end)
    end,

    OnAccept = function(self)
        SafeUI("NewThread.OnAccept", CreateThreadFromPopup, self)
    end,

    EditBoxOnEnterPressed = function(self)
        local dialog = self:GetParent()
        SafeUI("NewThread.OnEnter", CreateThreadFromPopup, dialog)
        if dialog then dialog:Hide() end
    end,

    EditBoxOnEscapePressed = function(self)
        pendingThreadQuestId = nil
        local dialog = self:GetParent()
        if dialog then dialog:Hide() end
    end,

    OnCancel = function()
        pendingThreadQuestId = nil
    end,
}

function AC.Journal.ShowThread(threadId)
    if not frame then BuildFrame() end
    currentView = "threads"
    detailStack = {}
    PushDetail(function() RenderThreadDetail(threadId) end)
    frame:Show()
end

function AC.Journal.Show(viewKey)
    if not frame then BuildFrame() end

    currentView = viewKey or currentView or "journey"
    detailStack = {}

    for key, button in pairs(navButtons) do
        if key == currentView then
            button:LockHighlight()
        else
            button:UnlockHighlight()
        end
    end

    local renderer = Render[currentView] or Render.journey
    local ok, err = pcall(renderer)
    if not ok then
        -- A broken view must not take the whole pane down with it.
        ShowListMode()
        SetHeader("Something went wrong")
        ShowDetailMode("The journal could not draw this view.\n\n" .. tostring(err))
        if AC.Debug and AC.Debug.Error then
            AC.Debug.Error("Journal." .. tostring(currentView), err)
        end
    end

    contentScroll:SetVerticalScroll(0)
    frame:Show()
end

function AC.Journal.Toggle(viewKey)
    if frame and frame:IsShown() then
        frame:Hide()
        return
    end
    AC.Journal.Show(viewKey)
end

function AC.Journal.IsShown()
    return frame and frame:IsShown()
end
