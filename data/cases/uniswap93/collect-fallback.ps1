$ErrorActionPreference = 'Stop'
$rawRoot = Join-Path $PSScriptRoot 'raw'
$observations = [System.Collections.Generic.List[object]]::new()
$txHash = '0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e'
$token = '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984'
$transferTopic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
function SaveRead([string]$Name,[string]$Endpoint,[string]$Method,[object[]]$Params) {
    if ($Method -notin @('eth_getTransactionReceipt','eth_getLogs','eth_call','eth_getBlockReceipts')) { throw 'Read-only method allowlist violation' }
    $request = @{jsonrpc='2.0';id=1;method=$Method;params=$Params} | ConvertTo-Json -Depth 10 -Compress
    [IO.File]::WriteAllText((Join-Path $rawRoot ($Name + '.request.json')),$request,[Text.UTF8Encoding]::new($false))
    $captured = [DateTime]::UtcNow.ToString('o')
    try {
        $response = Invoke-WebRequest -Uri $Endpoint -Method Post -Body $request -ContentType 'application/json' -TimeoutSec 25
        $responsePath = Join-Path $rawRoot ($Name + '.response.json')
        [IO.File]::WriteAllText($responsePath,$response.Content,[Text.UTF8Encoding]::new($false))
        $parsed = $response.Content | ConvertFrom-Json -Depth 100
        $kind = if ($null -ne $parsed.error) {'rpc_error'} elseif ($null -eq $parsed.result) {'null_result'} else {'result'}
        $observations.Add(@{name=$Name;method=$Method;endpoint=$Endpoint;captured_at_utc=$captured;http_status=[int]$response.StatusCode;result_kind=$kind;request_file=('raw/' + $Name + '.request.json');response_file=('raw/' + $Name + '.response.json');response_sha256=(Get-FileHash -LiteralPath $responsePath -Algorithm SHA256).Hash.ToLower()})
        Write-Output "$Name : $kind"
    } catch {
        $observations.Add(@{name=$Name;method=$Method;endpoint=$Endpoint;captured_at_utc=$captured;result_kind='transport_error';error=$_.Exception.Message;request_file=('raw/' + $Name + '.request.json')})
        Write-Output "$Name : transport_error"
    }
}
SaveRead 'publicnode-block-receipts' 'https://ethereum-rpc.publicnode.com' 'eth_getBlockReceipts' @('0x16fd58a')
SaveRead 'drpc-receipt' 'https://eth.drpc.org' 'eth_getTransactionReceipt' @($txHash)
SaveRead 'drpc-logs-all' 'https://eth.drpc.org' 'eth_getLogs' @(@{address=$token;fromBlock='0x16fd580';toBlock='0x16fd594';topics=@($transferTopic)})
SaveRead 'drpc-decimals' 'https://eth.drpc.org' 'eth_call' @(@{to=$token;data='0x313ce567'},'0x16fd58a')
SaveRead 'flashbots-receipt' 'https://rpc.flashbots.net' 'eth_getTransactionReceipt' @($txHash)
SaveRead 'tenderly-receipt' 'https://mainnet.gateway.tenderly.co' 'eth_getTransactionReceipt' @($txHash)
$capture = @{schema_version='cluetide.capture.v1';finished_at_utc=[DateTime]::UtcNow.ToString('o');observations=$observations}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'capture-fallback.json'),($capture | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
