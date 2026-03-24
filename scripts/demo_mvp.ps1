###############################################################################
# Shipyard MVP Demo Script
# Run this in PowerShell. Press Enter at each pause to proceed.
###############################################################################

$BASE_URL = "https://shipyard-production-610b.up.railway.app"
$LS_ORG = "9ec225d0-ceaf-4bba-a026-02438fa14772"
$LS_PROJECT = "6a036fa1-fcf9-4af7-8648-e2539bdb54ef"
$LS_URL = "https://smith.langchain.com/o/$LS_ORG/projects/p/$LS_PROJECT"

function Pause-Step {
    param(
        [string]$Message,
        [string]$Command = ""
    )
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host $Message -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    if ($Command) {
        Write-Host ""
        Write-Host "Command:" -ForegroundColor DarkYellow
        Write-Host "  $Command" -ForegroundColor White
        Write-Host ""
    }
    Write-Host "Press Enter to execute..." -ForegroundColor DarkGray
    Read-Host
}

function Poll-Task {
    param([string]$TaskId, [int]$MaxAttempts = 12)
    $attempt = 0
    do {
        Start-Sleep -Seconds 5
        $attempt++
        $r = Invoke-RestMethod -Uri "$BASE_URL/status/$TaskId"
        Write-Host "  Attempt $attempt - Status: $($r.status)" -ForegroundColor DarkGray
    } while ($r.status -notin @("completed", "failed") -and $attempt -lt $MaxAttempts)
    Write-Host ""
    Write-Host "Final status: $($r.status)" -ForegroundColor Green
    if ($r.result) { Write-Host ""; Write-Host $r.result -ForegroundColor White }
    if ($r.error) { Write-Host "Error: $($r.error)" -ForegroundColor Red }
}

function Show-FileWithHighlights {
    param(
        [string]$Content,
        [string]$RedPattern = "",
        [string]$GreenPattern = ""
    )
    Write-Host ""
    Write-Host "--- demo_workspace/user_service.py ---" -ForegroundColor Cyan
    foreach ($line in $Content -split "`n") {
        if ($GreenPattern -and $line -match $GreenPattern) {
            Write-Host $line -ForegroundColor Green
        } elseif ($RedPattern -and $line -match $RedPattern) {
            Write-Host $line -ForegroundColor Red
        } else {
            Write-Host $line -ForegroundColor Gray
        }
    }
    Write-Host "--------------------------------------" -ForegroundColor Cyan
    Write-Host ""
}

function Get-ServerFile {
    Write-Host ""
    Write-Host "Reading file from server..." -ForegroundColor DarkGray
    $b = @{ instruction = "Read demo_workspace/user_service.py. Reply with ONLY the raw file contents inside a single code block. No explanation, no commentary, just the code block." } | ConvertTo-Json
    $resp = Invoke-RestMethod -Uri "$BASE_URL/instruction" -Method Post -ContentType "application/json" -Body $b
    $tid = $resp.task_id
    $att = 0
    do {
        Start-Sleep -Seconds 5
        $att++
        $res = Invoke-RestMethod -Uri "$BASE_URL/status/$tid"
    } while ($res.status -notin @("completed", "failed") -and $att -lt 12)
    if ($res.result) {
        $text = $res.result
        # Extract content from code block if present
        if ($text -match '(?s)```(?:python)?\r?\n(.*?)\r?\n```') {
            return $matches[1]
        }
        # Otherwise return as-is
        return $text
    }
    return ""
}

###############################################################################
# SETUP
###############################################################################

Pause-Step -Message "SETUP: Reset the server to start with a clean session." `
           -Command "Invoke-RestMethod -Uri $BASE_URL/reset -Method Post"
Invoke-RestMethod -Uri "$BASE_URL/reset" -Method Post

Pause-Step -Message "SETUP: Create demo_workspace/user_service.py on the server." `
           -Command "POST $BASE_URL/instruction { instruction: 'Create user_service.py...' }"

$fileContent = @'
class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        query = f"SELECT * FROM users WHERE id = {user_id}"
        return self.db.execute(query)

    def create_user(self, name, email):
        query = f"INSERT INTO users (name, email) VALUES ('{name}', '{email}')"
        self.db.execute(query)
        return {"name": name, "email": email}

    def delete_user(self, user_id):
        query = f"DELETE FROM users WHERE id = {user_id}"
        self.db.execute(query)

    def list_users(self):
        return self.db.execute("SELECT * FROM users")
'@
$setupInstr = "Write a new file called demo_workspace/user_service.py with this EXACT content (do not add comments, do not modify anything):`n`n$fileContent"
$setupBody = @{ instruction = $setupInstr } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$BASE_URL/instruction" -Method Post -ContentType "application/json" -Body $setupBody
$taskId = $response.task_id
Write-Host "Task submitted: $taskId -- polling..." -ForegroundColor Green
Poll-Task -TaskId $taskId

Pause-Step -Message "SETUP: Reset session so the demo starts fresh (file stays on disk)." `
           -Command "Invoke-RestMethod -Uri $BASE_URL/reset -Method Post"
Invoke-RestMethod -Uri "$BASE_URL/reset" -Method Post
Write-Host "Session reset." -ForegroundColor Green
Clear-Host

###############################################################################
# PART 1: INTRODUCTION
###############################################################################

Pause-Step -Message "PART 1 - INTRODUCTION: Show the health endpoint to prove the server is live." `
           -Command "Invoke-RestMethod -Uri $BASE_URL/health"
$health = Invoke-RestMethod -Uri "$BASE_URL/health"
Write-Host "Health check:" -ForegroundColor Green
$health | Format-List

###############################################################################
# SHOW THE FILE BEFORE EDITING
###############################################################################

Pause-Step -Message "PREVIEW: Show the file with SQL injection vulnerabilities highlighted in red." `
           -Command "Get-ServerFile (read demo_workspace/user_service.py from server)"

$filePreview = Get-ServerFile
Show-FileWithHighlights -Content $filePreview -RedPattern 'query = f'

Write-Host "The lines in RED use f-string SQL queries -- vulnerable to SQL injection!" -ForegroundColor Yellow
Write-Host ""

Invoke-RestMethod -Uri "$BASE_URL/reset" -Method Post

###############################################################################
# PART 2: SURGICAL FILE EDITING
###############################################################################

Pause-Step -Message "PART 2 - SURGICAL EDITING: Fix ONLY the get_user SQL injection. Other methods must remain untouched." `
           -Command "POST $BASE_URL/instruction { instruction: 'Fix SQL injection in get_user only...' }"

$body = @{
    instruction = "Read demo_workspace/user_service.py and fix the SQL injection vulnerability in the get_user method. Use parameterized queries instead of f-strings. Only fix get_user - do not touch the other methods yet."
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "$BASE_URL/instruction" -Method Post -ContentType "application/json" -Body $body
$taskId = $response.task_id
Write-Host "Task submitted: $taskId -- polling..." -ForegroundColor Green
Poll-Task -TaskId $taskId

Pause-Step -Message "PART 2 - RESULT: Show the modified file. Fixed query in GREEN, remaining vulnerabilities in RED." `
           -Command "Get-ServerFile (read demo_workspace/user_service.py from server)"
$fileContent = Get-ServerFile
Show-FileWithHighlights -Content $fileContent -GreenPattern 'query = [^f]' -RedPattern 'query = f'
Write-Host "GREEN = fixed (parameterized query)  |  RED = still vulnerable (f-string)" -ForegroundColor Yellow

###############################################################################
# PART 3: CONTEXT INJECTION
###############################################################################

Pause-Step -Message "PART 3 - CONTEXT INJECTION: Fix remaining methods with injected coding standards." `
           -Command "POST $BASE_URL/instruction { instruction: '...', context: [{ type: 'spec', content: 'SQL Coding Standards...' }] }"

$standards = "SQL Coding Standards: 1) Always use parameterized queries with ? placeholders. 2) Use self.db.execute(query, params) where params is a tuple. 3) Add a docstring to every method. 4) Return None explicitly from delete operations."

$body = @{
    instruction = "Fix the SQL injection vulnerabilities in all remaining methods of demo_workspace/user_service.py."
    context = @(@{ type = "spec"; source = "coding_standards.md"; content = $standards })
} | ConvertTo-Json -Depth 3

$response = Invoke-RestMethod -Uri "$BASE_URL/instruction" -Method Post -ContentType "application/json" -Body $body
$taskId = $response.task_id
Write-Host "Task submitted: $taskId -- polling..." -ForegroundColor Green
Poll-Task -TaskId $taskId

Pause-Step -Message "PART 3 - RESULT: Show the fully updated file. All queries should now be in GREEN." `
           -Command "Get-ServerFile (read demo_workspace/user_service.py from server)"
$fileContent = Get-ServerFile
Show-FileWithHighlights -Content $fileContent -GreenPattern 'query = [^f]'
Write-Host "All queries are now GREEN -- parameterized and safe from SQL injection!" -ForegroundColor Yellow

###############################################################################
# PART 4: MULTI-AGENT COORDINATION
###############################################################################

Pause-Step -Message "PART 4 - MULTI-AGENT: Reset session, then dispatch researcher + coder via supervisor." `
           -Command "Invoke-RestMethod -Uri $BASE_URL/reset -Method Post"
Invoke-RestMethod -Uri "$BASE_URL/reset" -Method Post
Write-Host "Session reset." -ForegroundColor Green

Pause-Step -Message "PART 4 - MULTI-AGENT: Supervisor dispatches researcher first, then coder." `
           -Command "POST $BASE_URL/instruction { instruction: 'Investigate then add validation...', use_supervisor: true }"

$body = @{
    instruction = "First investigate demo_workspace/user_service.py to identify any remaining issues, then add input validation to create_user - name must be non-empty and email must contain an @ symbol."
    use_supervisor = $true
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "$BASE_URL/instruction" -Method Post -ContentType "application/json" -Body $body
$taskId = $response.task_id
Write-Host "Task submitted: $taskId -- polling..." -ForegroundColor Green
Poll-Task -TaskId $taskId -MaxAttempts 24

###############################################################################
# PART 5: TRACING
###############################################################################

Pause-Step -Message "PART 5 - TRACING: Open the LangSmith dashboard in your browser." `
           -Command "Start-Process $LS_URL"

Start-Process $LS_URL

Write-Host "Shared trace links:" -ForegroundColor Green
Write-Host "  Trace 1 (normal run):" -ForegroundColor White
Write-Host "    https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r" -ForegroundColor White
Write-Host "  Trace 2 (error/recovery):" -ForegroundColor White
Write-Host "    https://smith.langchain.com/public/24b479c5-a023-4855-947b-92914f28a0e6/r" -ForegroundColor White

###############################################################################
# PART 6: PERSISTENT LOOP
###############################################################################

Pause-Step -Message "PART 6 - PERSISTENT LOOP: Show conversation history persisted across requests." `
           -Command "Invoke-RestMethod -Uri $BASE_URL/history"

$history = Invoke-RestMethod -Uri "$BASE_URL/history"
Write-Host "Total messages in session: $($history.total)" -ForegroundColor Green
Write-Host ""
foreach ($msg in $history.messages) {
    $c = $msg.content
    if ($c.Length -gt 120) { $c = $c.Substring(0, 120) + "..." }
    Write-Host "  [$($msg.type)] $c" -ForegroundColor DarkGray
}

###############################################################################
# CLOSING
###############################################################################

Pause-Step -Message "CLOSING: Open the GitHub repo in the browser." `
           -Command "Start-Process https://github.com/lramosve/shipyard"
Start-Process "https://github.com/lramosve/shipyard"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Demo complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Shipyard - Autonomous Coding Agent" -ForegroundColor White
Write-Host "  GitHub:    https://github.com/lramosve/shipyard" -ForegroundColor White
Write-Host "  Live API:  $BASE_URL" -ForegroundColor White
Write-Host "  API Docs:  $BASE_URL/docs" -ForegroundColor White
Write-Host "  LangSmith: $LS_URL" -ForegroundColor White
Write-Host ""
