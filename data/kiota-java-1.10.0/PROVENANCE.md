# Provenance of this dataset

`refactoring/CloneDeMocker+Terra-5.6/` (round 1, gpt-5.6-terra) was produced on a checkout
detected as `data/kiota-java` and was merged here on 2026-09-24 (its old directory removed).

- Detection re-run on kiota-java **v1.10.0** (latest release) with the same parameters found
  the identical MCI set: 76 mock objects, 14 MCIs, same mocked class / test file / test method
  / variable for every MCI.
- MCI ids were matched **by content, not by id**: the two detections numbered two MCIs the
  other way round, so these round-1 rows were renamed: {"com.microsoft.kiota.serialization.SerializationWriter::1": "com.microsoft.kiota.serialization.SerializationWriter::2", "com.microsoft.kiota.serialization.SerializationWriter::2": "com.microsoft.kiota.serialization.SerializationWriter::1"}.
  Each row keeps `sourceDataset` and `originalMciId`.
- Caveat: round 1's exact checkout was not recorded. Every detected statement and test
  method matches both v1.9.3 and v1.10.0 source (whitespace-normalised); four of the eight
  affected test files differ between those versions, but not in the detected methods.
- Round 1 ran with PIT (4 checks). 13 of its 14 rows carry their own tokens and generation
  time; one (`Response::1`) is a cache replay from the PIT re-run and has no timing of its own.

`CloneDeMocker+Luna-5.6` and `CloneDeMocker-V1+Luna-5.6` (rounds 2 and 3) were run on v1.10.0.
