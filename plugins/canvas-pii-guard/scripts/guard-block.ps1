<#
  guard-block.ps1  (canvas-pii-guard) - PreToolUse hook.
  Reads the pending tool call (JSON on stdin), and if it targets a Canvas student-data
  endpoint or a local student-data cache, returns a DENY decision so the call never
  runs. This is the prevention guarantee: blocked calls never reach the network, so no
  student PII is acquired or transmitted.

  Contract: read hook payload JSON from stdin; to deny, print a PreToolUse JSON
  decision and exit 0; to allow, print nothing and exit 0.
  On an internal/parse error it fails OPEN (allows) and warns on stderr, so a malformed
  payload cannot brick all tooling -- the broader guarantee also rests on the content
  skill shipping no PII tools and (for rollout) managed-settings enforcement.
#>
. "$PSScriptRoot\PiiPatterns.ps1"

function Get-StringValues {
    param($node, [System.Collections.ArrayList]$acc)
    if ($null -eq $node) { return }
    if ($node -is [string]) { [void]$acc.Add($node); return }
    if ($node -is [System.Collections.IEnumerable] -and -not ($node -is [string])) {
        foreach ($item in $node) { Get-StringValues $item $acc }
        return
    }
    if ($node.PSObject -and $node.PSObject.Properties) {
        foreach ($p in $node.PSObject.Properties) { Get-StringValues $p.Value $acc }
    }
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
    $acc = New-Object System.Collections.ArrayList
    Get-StringValues $payload.tool_input $acc
    $text = ($acc -join ' ')

    $res = Test-CanvasCallAllowed -Text $text
    if (-not $res.allowed) {
        $out = @{ hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = 'deny'
            permissionDecisionReason = $res.reason
        } }
        $out | ConvertTo-Json -Compress -Depth 5
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine("canvas-pii-guard: guard-block internal error (failing open): $($_.Exception.Message)")
    exit 0
}
