import pytest

from hyperlapse.config import SelectionWeights
from hyperlapse.core.select import choose, viterbi

W = SelectionWeights()


def cand(i, seq, t=0, along=0.0, score=0.1, pano=False):
    return {"id": i, "sequence_id": seq, "captured_at": t, "along_m": along, "score": score, "is_pano": pano}


def test_viterbi_prefers_continuous_sequence():
    # Sequence "b" is marginally better per sample, but switching costs more than it gains.
    cands = []
    for k in range(10):
        a = cand(f"a{k}", "a", along=k * 15, score=0.30)
        b = cand(f"b{k}", "b", along=k * 15, score=0.25 if k != 5 else 5.0)
        cands.append([b, a])
    chosen = choose(cands, fixed={}, excluded=set(), w=W)
    seqs = {c["sequence_id"] for c in chosen}
    assert len(seqs) == 1


def test_viterbi_greedy_when_no_transition_cost():
    states = [[cand("x", "s", score=1.0), cand("y", "s", score=0.0)] for _ in range(3)]
    for k, st in enumerate(states):
        for c in st:
            c["id"] = f"{c['id']}{k}"
            c["along_m"] = k * 10
    path = viterbi(states, [[c["score"] for c in s] for s in states], W)
    assert path == [1, 1, 1]


def test_backwards_penalised():
    cands = [
        [cand("a0", "s", along=0)],
        [cand("back", "s", along=-20, score=0.0), cand("fwd", "s", along=15, score=0.4)],
        [cand("a2", "s", along=30)],
    ]
    chosen = choose(cands, fixed={}, excluded=set(), w=W)
    assert chosen[1]["id"] == "fwd"


def test_fixed_excluded_and_gaps():
    cands = [[cand(f"a{k}", "a", along=k * 15)] for k in range(6)]
    cands[2] = []
    fixed = {4: cand("pinned", "z", along=60, score=9)}
    chosen = choose(cands, fixed=fixed, excluded={1}, w=W)
    assert chosen[1] is None and chosen[2] is None
    assert chosen[4]["id"] == "pinned"
    assert chosen[0]["id"] == "a0" and chosen[5]["id"] == "a5"


def test_duplicate_consecutive_kept_once():
    same = cand("dup", "a", along=10)
    cands = [[same], [dict(same)], [cand("c", "a", along=30)]]
    chosen = choose(cands, fixed={}, excluded=set(), w=W)
    assert [c and c["id"] for c in chosen] == ["dup", None, "c"]


def test_camera_type_switch_penalised():
    # A slightly better pano mid-route is not worth two flat <-> 360 switches.
    cands = [[cand(f"a{k}", "a", along=k * 15, score=0.3)] for k in range(5)]
    cands[2] = [cand("pano", "a", along=30, score=0.0, pano=True), cand("flat", "a", along=30, score=0.5)]
    chosen = choose(cands, fixed={}, excluded=set(), w=W)
    assert chosen[2]["id"] == "flat"


def test_transition_matrix_matches_scalar():
    from hyperlapse.core.select import transition_cost, transition_matrix

    prev = [cand("x", "s", t=0, along=10), cand("y", "t", t=10**10, along=40, pano=True)]
    cur = [cand("x", "s", t=0, along=20), cand("z", "t", t=5 * 10**9, along=5, pano=True)]
    m = transition_matrix(prev, cur, W)
    for i, a in enumerate(prev):
        for j, b in enumerate(cur):
            assert m[i, j] == pytest.approx(transition_cost(a, b, W))
