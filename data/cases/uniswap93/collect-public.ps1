param([string]$Endpoint = 'https://ethereum-rpc.publicnode.com')
$ErrorActionPreference = 'Stop'
$caseRoot = $PSScriptRoot
$rawRoot = Join-Path $caseRoot 'raw'
New-Item -ItemType Directory -Force -Path $rawRoot | Out-Null
$txHash = '0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e'
$token = '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984'
$timelock = '0x1a9c8182c09f50c8318d769245bea52c32be35bc'
$transferTopic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
$timelockTopic = '0x0000000000000000000000001a9c8182c09f50c8318d769245bea52c32be35bc'
$started = [DateTime]::UtcNow.ToString('o')
$observations = [System.Collections.Generic.List[object]]::new()
function SaveRpc([string]$Name,[string]$Method,[object[]]$Params) {
    if ($Method -notin @('eth_chainId','eth_getTransactionReceipt','eth_getTransactionByHash','eth_getBlockByNumber','eth_getLogs','eth_call','eth_getBlockReceipts')) { throw 'Read-only method allowlist violation' }
    $request = @{jsonrpc='2.0';id=1;method=$Method;params=$Params} | ConvertTo-Json -Depth 10 -Compress
    $requestPath = Join-Path $rawRoot ($Name + '.request.json')
    [IO.File]::WriteAllText($requestPath,$request,[Text.UTF8Encoding]::new($false))
    $captured = [DateTime]::UtcNow.ToString('o')
    try {
        $response = Invoke-WebRequest -Uri $Endpoint -Method Post -Body $request -ContentType 'application/json' -TimeoutSec 35
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
SaveRpc 'chain-id' 'eth_chainId' @()
SaveRpc 'transaction' 'eth_getTransactionByHash' @($txHash)
SaveRpc 'receipt' 'eth_getTransactionReceipt' @($txHash)
SaveRpc 'case-block' 'eth_getBlockByNumber' @('0x16fd58a',$false)
SaveRpc 'start-block' 'eth_getBlockByNumber' @('0x16fd580',$false)
SaveRpc 'end-block' 'eth_getBlockByNumber' @('0x16fd594',$false)
SaveRpc 'finalized' 'eth_getBlockByNumber' @('finalized',$false)
SaveRpc 'logs-all' 'eth_getLogs' @(@{address=$token;fromBlock='0x16fd580';toBlock='0x16fd594';topics=@($transferTopic)})
SaveRpc 'logs-out' 'eth_getLogs' @(@{address=$token;fromBlock='0x16fd580';toBlock='0x16fd594';topics=@($transferTopic,$timelockTopic)})
SaveRpc 'logs-in' 'eth_getLogs' @(@{address=$token;fromBlock='0x16fd580';toBlock='0x16fd594';topics=@($transferTopic,$null,$timelockTopic)})
SaveRpc 'decimals' 'eth_call' @(@{to=$token;data='0x313ce567'},'0x16fd58a')
SaveRpc 'total-supply-before' 'eth_call' @(@{to=$token;data='0x18160ddd'},'0x16fd589')
SaveRpc 'total-supply-after' 'eth_call' @(@{to=$token;data='0x18160ddd'},'0x16fd58a')
$capture = @{schema_version='cluetide.capture.v1';started_at_utc=$started;finished_at_utc=[DateTime]::UtcNow.ToString('o');endpoint=$Endpoint;observations=$observations}
[IO.File]::WriteAllText((Join-Path $caseRoot 'capture.json'),($capture | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
