# Security Policy

## Preview support scope

MycEvo is currently a technical preview. Security fixes for the public single-user engine are not intended to be paywalled.

## Report privately

Do not open a public issue containing credentials, private prompts, unpublished research, task traces, personal paths or exploitable details. Before public release, the owner must configure a private security contact or GitHub private vulnerability reporting and replace this paragraph with the verified channel.

## Never include in a report

- API keys, access tokens or `.env` files;
- private registry, prompt, run or trace contents;
- raw experimental or customer data;
- databases, model weights or proprietary artifacts;
- absolute user paths when a sanitized reproduction is possible.

## Relevant boundaries

- automatic writeback must remain candidate-first;
- canonical promotion requires an explicit human decision;
- explicit workspace roots must isolate all writes;
- public-engine tests must never mutate a private instance.
