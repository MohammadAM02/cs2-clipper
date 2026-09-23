"""The analyzed FACEIT match (aea4e59ccfc6c962, de_inferno, 13-5) as the round facts CS:DM's
database holds for the subject. tests/test_csdm_db.py checks the live database still says exactly this."""

from clipper.model import RoundFacts

SUBJECT = "76561198192858303"
MATCH_CHECKSUM = "aea4e59ccfc6c962"
DEMO_NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1"

MATCH_FACTS: tuple[RoundFacts, ...] = (
    RoundFacts(round=1, frag_ticks=(10363, 10520), headshots=2, round_won=True,
               round_start_tick=7538, round_end_tick=11223),
    RoundFacts(round=2, frag_ticks=(14512,), round_won=True,
               round_start_tick=11671, round_end_tick=14811),
    RoundFacts(round=3, frag_ticks=(19960, 20957, 21195), headshots=1, round_won=True,
               round_start_tick=15259, round_end_tick=21195),
    RoundFacts(round=4, frag_ticks=(25053, 26147, 28841), headshots=1, bot_frags=3, round_won=True,
               round_start_tick=21643, round_end_tick=29836),
    RoundFacts(round=5, frag_ticks=(33049, 33192), headshots=1, round_won=False,
               round_start_tick=30284, round_end_tick=37288),
    RoundFacts(round=6, frag_ticks=(39688, 41950), headshots=1, bot_frags=1, round_won=True,
               round_start_tick=37736, round_end_tick=42028),
    RoundFacts(round=7, frag_ticks=(45542,), smoke_frags=1, bot_frags=1, round_won=False,
               round_start_tick=42476, round_end_tick=47662),
    RoundFacts(round=8, frag_ticks=(52636, 52951, 53020), headshots=2, round_won=True,
               round_start_tick=48110, round_end_tick=53020),
    RoundFacts(round=9, frag_ticks=(56581,), headshots=1, round_won=True,
               round_start_tick=53468, round_end_tick=58107),
    RoundFacts(round=10, frag_ticks=(61489, 64547), bot_frags=1, round_won=True,
               round_start_tick=58555, round_end_tick=67211),
    RoundFacts(round=11, frag_ticks=(69972,), round_won=True,
               round_start_tick=67659, round_end_tick=71583),
    RoundFacts(round=12, frag_ticks=(74135, 76199, 76431, 78361), blind_frags=1, round_won=False,
               round_start_tick=72031, round_end_tick=81477),
    RoundFacts(round=14, frag_ticks=(92231, 92308, 93894), headshots=1, round_won=True,
               round_start_tick=90164, round_end_tick=93894),
    RoundFacts(round=15, frag_ticks=(97314, 97633, 100465), headshots=1, bot_frags=1, round_won=True,
               round_start_tick=94342, round_end_tick=100465),
    RoundFacts(round=18, frag_ticks=(118270, 120879, 123968), headshots=1, round_won=True,
               round_start_tick=116005, round_end_tick=123968),
)
