Killed the A=B run (background shell 6bb1e9): confirmed terminated before any publish.
Live catch-up will export ONCHAIN_RPC_URL_A=$ALCHEMY_POL_ENDPOINT and ONCHAIN_RPC_URL_B=$ALCHEMY_POL_ENDPOINT_RESERVE — two independent Alchemy accounts, values never printed or committed.
No publicnode/drpc/1rpc or any other public RPC will be used; earlier probes against them were diagnostic only and are abandoned.
Collector code already filters on the V2 exchanges 0xE111180000d2663C0091e4f400237545B87B996B / 0xe2222d279d744050d28e00520010520000310F59 (onchain-decode.ts); my manual curl probes used stale addresses — code needed no change.
Free-tier getLogs caps surface as RpcRangeTooWide; fetchLogsRange will halve on both real endpoints as designed — no duplication of endpoint A.
