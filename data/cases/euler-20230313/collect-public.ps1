param([ValidateSet('Initial','Window','State')][string]$Phase = 'Initial')
$ErrorActionPreference = 'Stop'
$endpoint = 'https://mainnet.gateway.tenderly.co'
$transactionHash = '0xc310a0affe2169d1f6feec1c63dbc7f7c62a887fa48795d327d4d2da2d6b111d'
$rawRoot = Join-Path $PSScriptRoot 'raw'
[IO.Directory]::CreateDirectory($rawRoot) | Out-Null
$observations = [System.Collections.Generic.List[object]]::new()
function Read-PublicRpc([string]$Name, [string]$Method, [object[]]$Arguments) {
    if ($Method -notin @('eth_chainId','eth_getTransactionReceipt','eth_getTransactionByHash','eth_getBlockByNumber','eth_getLogs','eth_call')) { throw 'Unsupported public read' }
    $body = @{jsonrpc='2.0';id=1;method=$Method;params=$Arguments} | ConvertTo-Json -Depth 30 -Compress
    $requestFile = 'raw/' + $Name + '.request.json'
    $responseFile = 'raw/' + $Name + '.response.json'
    if ((Test-Path -LiteralPath (Join-Path $PSScriptRoot $requestFile)) -or (Test-Path -LiteralPath (Join-Path $PSScriptRoot $responseFile))) {
        throw 'Capture files already exist; preserve this snapshot and use a fresh case copy for another capture.'
    }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $requestFile),$body,[Text.UTF8Encoding]::new($false))
    $captured = [DateTime]::UtcNow.ToString('o')
    $entry = @{name=$Name;method=$Method;endpoint=$endpoint;captured_at_utc=$captured;request_file=$requestFile;request_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $requestFile) -Algorithm SHA256).Hash.ToLower()}
    try {
        $response = Invoke-WebRequest -Uri $endpoint -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 25 -SkipHttpErrorCheck
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot $responseFile),$response.Content,[Text.UTF8Encoding]::new($false))
        $parsed = $response.Content | ConvertFrom-Json -Depth 100
        $kind = if ([int]$response.StatusCode -ne 200) {'http_error'} elseif ($null -ne $parsed.error) {'rpc_error'} elseif ($null -eq $parsed.result) {'null_result'} else {'result'}
        $entry.http_status = [int]$response.StatusCode
        $entry.result_kind = $kind
        $entry.response_file = $responseFile
        $entry.response_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $responseFile) -Algorithm SHA256).Hash.ToLower()
        $observations.Add($entry)
        Write-Output ($Name + ': ' + $kind)
    } catch {
        $entry.result_kind = 'transport_error'
        $entry.error = 'Public read transport failed; TLS validation was preserved.'
        $observations.Add($entry)
        Write-Output ($Name + ': transport_error')
    }
}
if ($Phase -eq 'Initial') {
    Read-PublicRpc 'chain-id' 'eth_chainId' @()
    Read-PublicRpc 'receipt' 'eth_getTransactionReceipt' @($transactionHash)
    Read-PublicRpc 'transaction' 'eth_getTransactionByHash' @($transactionHash)
    Read-PublicRpc 'finalized' 'eth_getBlockByNumber' @('finalized',$false)
} elseif ($Phase -eq 'Window') {
    $receipt = (Get-Content -Raw -LiteralPath (Join-Path $rawRoot 'receipt.response.json') | ConvertFrom-Json -Depth 100).result
    if ($null -eq $receipt -or $receipt.transactionHash -ne $transactionHash) { throw 'Verified source receipt is required' }
    $block = [Convert]::ToInt64($receipt.blockNumber.Substring(2),16)
    $start = '0x' + ($block - 1).ToString('x')
    $end = '0x' + ($block + 1).ToString('x')
    $token = '0x6b175474e89094c44da98b954eedeac495271d0f'
    $subject = '0x27182842e098f60e3d576794a5bffb0777e025d3'
    $transfer = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    $subjectTopic = '0x' + ('0' * 24) + $subject.Substring(2)
    Read-PublicRpc 'start-block' 'eth_getBlockByNumber' @($start,$false)
    Read-PublicRpc 'case-block' 'eth_getBlockByNumber' @($receipt.blockNumber,$false)
    Read-PublicRpc 'end-block' 'eth_getBlockByNumber' @($end,$false)
    Read-PublicRpc 'logs-out' 'eth_getLogs' @(@{address=$token;fromBlock=$start;toBlock=$end;topics=@($transfer,$subjectTopic,$null)})
    Read-PublicRpc 'logs-in' 'eth_getLogs' @(@{address=$token;fromBlock=$start;toBlock=$end;topics=@($transfer,$null,$subjectTopic)})
    $header = (Get-Content -Raw -LiteralPath (Join-Path $rawRoot 'end-block.response.json') | ConvertFrom-Json -Depth 100).result
    if ($null -ne $header -and $header.number -eq $end) {
        $anchor = @{blockHash=$header.hash;requireCanonical=$true}
        foreach ($item in @(@{name='metadata-decimals';selector='0x313ce567'},@{name='metadata-symbol';selector='0x95d89b41'},@{name='metadata-name';selector='0x06fdde03'})) {
            Read-PublicRpc $item.name 'eth_call' @(@{to=$token;data=$item.selector},$anchor)
        }
    }
} else {
    $token = '0x6b175474e89094c44da98b954eedeac495271d0f'
    foreach ($item in @(@{name='state-total-supply-before';header='start-block'},@{name='state-total-supply-after';header='case-block'})) {
        $header = (Get-Content -Raw -LiteralPath (Join-Path $rawRoot ($item.header + '.response.json')) | ConvertFrom-Json -Depth 100).result
        if ($null -eq $header) { throw 'Captured historical header is required' }
        $anchor = @{blockHash=$header.hash;requireCanonical=$true}
        Read-PublicRpc $item.name 'eth_call' @(@{to=$token;data='0x18160ddd'},$anchor)
    }
}
$capture = @{schema_version='cluetide.public-capture/v1';phase=$Phase;finished_at_utc=[DateTime]::UtcNow.ToString('o');observations=$observations;read_attempts=$observations.Count;model_requests=0;transactions_signed_or_broadcast=0}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot ('capture-' + $Phase.ToLower() + '.json')),($capture | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
