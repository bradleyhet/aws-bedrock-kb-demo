# Remote access and VPN (AFS-IT-011)

> Sample handbook page for the kb-demo app. Acme Field Services and everything in it are fictional.

Owner: IT. Last reviewed: May 2026.

## When you need the VPN

Connect to Acme Connect, the company VPN, before you open the job system, the drawings library or
any internal site. Email and Teams work without it.

## Signing in

- Sign in with your Acme account, then approve the prompt in the Acme Authenticator app by typing
  the two-digit number shown on screen.
- A session stays connected for up to 12 hours, then asks you to sign in again.
- An idle session disconnects after 45 minutes without traffic.

## Gateways

Acme Connect picks the nearest gateway automatically. If the connection is slow, choose one by hand:

- Sydney: syd-gw2
- Melbourne: mel-gw1
- Perth: per-gw1

## On client sites

- Many client networks block VPN traffic. If Acme Connect can't connect within 2 minutes, switch to
  your phone's hotspot; the expenses policy (AFS-FIN-004) covers the data.
- Plug your laptop into a client's network socket only with the client's written permission.
- Hotel and airport Wi-Fi is fine, as long as the VPN is connected before you start work.

## Split tunnelling

Split tunnelling is off: while you're connected, all your traffic goes through Acme. Streaming
video over the VPN is blocked.

## Troubleshooting

1. Check the date and time on your laptop. A clock more than 5 minutes out stops you signing in.
2. Restart Acme Connect from the system tray.
3. Try another gateway.
4. Call the Service Desk on extension 4100, or 02 5550 4100 from outside the network.
