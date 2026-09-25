# V2 + Terra without the repair loop (inferred from round 1)

Source: `CloneDeMocker+Terra-5.6` rows on github/main 27edca24. Nothing was re-run. A row with repairRounds 0 keeps its verdict; a row that needed repair counts as a failed first attempt. spring-integration has no Terra round 1.

| Project | MCIs | SUCCESS with loop | SUCCESS without loop | Rescued by loop | Failed despite loop | Cache-hit rows |
|---|---:|---:|---:|---:|---:|---:|
| kiota-java-1.10.0 | 14 | 14 (100.0%) | 14 (100.0%) | 0 | 0 | 1 |
| dubbo-3.3.6 | 99 | 96 (97.0%) | 82 (82.8%) | 14 | 2 | 1 |
| druid-37.0.0 | 87 | 83 (95.4%) | 67 (77.0%) | 16 | 0 | 0 |
| spring-security-7.1.1 | 314 | 305 (97.1%) | 280 (89.2%) | 25 | 2 | 19 |
| cloudstack | 1827 | 1685 (92.2%) | 1511 (82.7%) | 174 | 29 | 130 |
| **Total** | 2341 | 2183 (93.3%) | 1954 (83.5%) | 229 | 33 | 151 |

Cache-hit rows replayed an earlier stored answer in round 1; they are counted like the rest.
CloudStack's round 1 is still being corrected by C (A-052); re-run this script when that is done.
