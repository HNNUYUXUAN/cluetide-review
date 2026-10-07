$ErrorActionPreference = 'Stop'
$rawRoot = Join-Path $PSScriptRoot 'raw'
$endpoint = 'https://mainnet.gateway.tenderly.co'
$token = '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984'
$endBlock = (Get-Content -Raw -LiteralPath (Join-Path $rawRoot 'end-block.response.json') | ConvertFrom-Json -Depth 100).result
$blockAnchor = @{blockHash=$endBlock.hash;requireCanonical=$true}
$observations = [System.Collections.Generic.List[object]]::new()
foreach ($query in @(@{name='metadata-end-decimals';selector='0x313ce567'},@{name='metadata-end-symbol';selector='0x95d89b41'},@{name='metadata-end-name';selector='0x06fdde03'})) {
    $request = @{jsonrpc='2.0';id=1;method='eth_call';params=@(@{to=$token;data=$query.selector},$blockAnchor)} | ConvertTo-Json -Depth 10 -Compress
    [IO.File]::WriteAllText((Join-Path $rawRoot ($query.name + '.request.json')),$request,[Text.UTF8Encoding]::new($false))
    $captured = [DateTime]::UtcNow.ToString('o')
    try {
        $response = Invoke-WebRequest -Uri $endpoint -Method Post -Body $request -ContentType 'application/json' -TimeoutSec 25 -SkipHttpErrorCheck
        $responsePath = Join-Path $rawRoot ($query.name + '.response.json')
        [IO.File]::WriteAllText($responsePath,$response.Content,[Text.UTF8Encoding]::new($false))
        $parsed = $response.Content | ConvertFrom-Json -Depth 100
        $kind = if ($null -ne $parsed.error) {'rpc_error'} elseif ($null -eq $parsed.result) {'null_result'} else {'result'}
        $observations.Add(@{name=$query.name;method='eth_call';selector=$query.selector;endpoint=$endpoint;captured_at_utc=$captured;block_number_hex=$endBlock.number;block_hash=$endBlock.hash;block_selection='EIP-1898';require_canonical=$true;http_status=[int]$response.StatusCode;result_kind=$kind;request_file=('raw/' + $query.name + '.request.json');response_file=('raw/' + $query.name + '.response.json');response_sha256=(Get-FileHash -LiteralPath $responsePath -Algorithm SHA256).Hash.ToLower()})
        Write-Output "$($query.name) : $kind"
    } catch {
        $observations.Add(@{name=$query.name;method='eth_call';selector=$query.selector;endpoint=$endpoint;captured_at_utc=$captured;block_number_hex=$endBlock.number;block_hash=$endBlock.hash;block_selection='EIP-1898';require_canonical=$true;result_kind='transport_error';error=$_.Exception.Message;request_file=('raw/' + $query.name + '.request.json')})
        Write-Output "$($query.name) : transport_error"
    }
}
$capture = @{schema_version='cluetide.capture.v1';finished_at_utc=[DateTime]::UtcNow.ToString('o');observations=$observations;read_attempts=3}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'capture-metadata.json'),($capture | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
