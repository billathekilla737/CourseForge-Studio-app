<#
  guard-redact.ps1  (canvas-pii-guard) - PostToolUse hook. SECONDARY / BEST-EFFORT.
  Scrubs structured PII patterns from a tool's output before the model sees it. This
  is a backstop, NOT the guarantee: the guarantee is the PreToolUse block (prevention).
  Output-rewriting by PostToolUse hooks is version-dependent in Claude Code -- VERIFY it
  takes effect on your version. The redaction engine itself (Invoke-PiiRedaction) is
  proven by tests/Run-GuardTests.ps1 and is also used by the sterilizing gateway, so it
  works regardless of whether the hook plumbing is honored.

  Fails open on error (emits nothing) so it cannot brick tooling.
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

    # Find the tool result text (field name varies across versions).
    $resultNode = $null
    foreach ($name in 'tool_response','tool_output','tool_result','response','output') {
        if ($payload.PSObject.Properties.Name -contains $name) { $resultNode = $payload.$name; break }
    }
    if ($null -eq $resultNode) { exit 0 }

    $acc = New-Object System.Collections.ArrayList
    Get-StringValues $resultNode $acc
    $text = ($acc -join "`n")
    if ([string]::IsNullOrEmpty($text)) { exit 0 }

    $r = Invoke-PiiRedaction -Text $text -Profile 'Standard'
    if ($r.Count -gt 0) {
        $out = @{ hookSpecificOutput = @{
            hookEventName = 'PostToolUse'
            additionalContext = "canvas-pii-guard: detected $($r.Count) structured-PII pattern(s) in this output and redacted them. (Backstop only; the primary control is the PreToolUse block.)"
            toolOutput = @{ stdout = $r.Text }
        } }
        $out | ConvertTo-Json -Compress -Depth 6
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine("canvas-pii-guard: guard-redact internal error (failing open): $($_.Exception.Message)")
    exit 0
}
