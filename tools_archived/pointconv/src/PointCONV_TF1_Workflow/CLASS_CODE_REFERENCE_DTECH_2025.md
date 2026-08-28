# DTECH 2025 Class Code Reference

This file is the local class-code reference derived from:

- `E:\Image_PointCloud_Segmentation\DTECH_Otter_Creek\DTECH_2025\ClassifiedLAS\SDAI_Classification_CLASSCODES_v4.xlsx`

It is intended to keep the PointCONV and Pointcept planning docs aligned to the same source class definitions.

## Raw Class Table

| Class | Label | Notes |
| --- | --- | --- |
| `0` | `Unclassified` |  |
| `1` | `Unassigned` |  |
| `2` | `Ground` |  |
| `3` | `Low Vegetation` |  |
| `4` | `Medium Vegetation` |  |
| `5` | `High Vegetation` |  |
| `6` | `Building` |  |
| `7` | `Noise: Low Point` |  |
| `8` | `Noise: High Point` |  |
| `9` | `Water` |  |
| `10` | `Misc` | Building particulars, piles and etc. |
| `14` | `Wire - General` | Primary, secondary, neutral, and communication conductors. |
| `15` | `Pole - Transmission Tower` |  |
| `18` | `Pole - electrical / utility` | Utility poles. |
| `19` | `Pole - Street / traffic` | Streetlight poles and traffic-light poles. |
| `20` | `Pole - Other` | Poles other than utility, streetlight, or traffic-light poles. |
| `23` | `Fence` |  |
| `24` | `Traffic Sign` |  |
| `26` | `Vehicle - any, other than car / truck` |  |
| `27` | `Car` |  |
| `28` | `Truck` |  |
| `30` | `Wire - Transmission` |  |
| `31` | `Wire - Guy wire` | Down guys, overhead guys, span guys, sidewalk braces. |
| `40` | `Road / Pavement` |  |
| `41` | `Rail` |  |
| `42` | `Road / Gravel roadedge` |  |
| `43` | `Railway Blast` |  |
| `46` | `Utility substation` |  |
| `50` | `Bridge Deck` |  |
| `51` | `Sidewalk / Concrete` |  |
| `52` | `Street Curb` |  |
| `54` | `Guard Rail` |  |
| `61` | `Bench / Seating` |  |
| `62` | `Bus Shelter` |  |
| `63` | `Mail Box (urban)` |  |
| `64` | `Bollards` |  |
| `67` | `Mail Box (rural)` |  |
| `70` | `Street Light Arm` |  |
| `71` | `Street Light Head` |  |
| `72` | `Electrical Pole Transformers` |  |
| `73` | `Electrical Pole Cross Bar (Cross Arm)` |  |
| `74` | `Electrical Insulators` |  |
| `75` | `Traffic Light Arm` |  |
| `76` | `Traffic Light` | Traffic-light attachment on the pole. |
| `77` | `LRT Pole Arm` |  |
| `79` | `Bell Pedestal (BPED)` |  |
| `80` | `Pad Mounted Transformer` |  |
| `81` | `TV / Telecom Pedestal (TVPED)` |  |
| `82` | `Pad Mounted Cabinet (OPI)` |  |
| `83` | `Flush to Grade Box (GLB)` | Skip if not reliably distinguishable. |
| `84` | `Electrical Power Meter` |  |
| `86` | `Traffic Control Box` |  |
| `87` | `Riser` |  |
| `89` | `Fuse/Switch` |  |
| `90` | `Cable TV Power Supply (Pole Mounted)` |  |
| `91` | `Transformer (Pole)` |  |
| `92` | `Sign / Post` |  |
| `93` | `Fire Hydrants` |  |
| `95` | `Submersible Transformer` | Skip if not reliably distinguishable. |

## Current PointCONV Binary Labels

The current binary and layered workflows use a subset of the above classes:

- `2` = `Ground`
- `5` = `High Vegetation`
- `14` = `Wire - General`
- `18` = `Pole - electrical / utility`

That means the current vegetation model is aligned specifically to `High Vegetation`, not to the broader `3/4/5` vegetation family.

## Recommended Utility6Class Collapse

For the custom Pointcept `utility6class` scaffold, the current recommended starting collapse is:

| Target class | Target id | Source LAS classes |
| --- | --- | --- |
| `ground` | `0` | `2, 40, 41, 42, 43, 50, 51, 52` |
| `wire` | `1` | `14, 30, 31` |
| `vegetation` | `2` | `3, 4, 5` |
| `pole` | `3` | `18, 19, 20, 72, 73, 74, 90, 91` |
| `tower` | `4` | `15` |
| `manmade` | `5` | `6, 10, 23, 24, 46, 54, 61, 62, 63, 64, 67, 70, 71, 75, 76, 77, 79, 80, 81, 82, 83, 84, 86, 87, 89, 92, 93, 95` |

Recommended default ignore bucket for the first utility6class pass:

- `0, 1, 7, 8, 9, 26, 27, 28`
- any future source class not explicitly mapped

This is a recommended starting collapse for model development, not a claim that the semantic grouping is final.

Two important judgment calls in this collapse are:

- roadway and hard-surface support classes are folded into `ground` to keep the first taxonomy compact
- utility-pole support attachments such as transformers, crossarms, insulators, and pole-mounted transformer-style devices are folded into `pole`, while streetlight and traffic-light attachment classes stay under `manmade`
