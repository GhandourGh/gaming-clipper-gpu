"""AI mix: reaction clips get the framed cam, action clips the hook card, every layout is used and neighbours differ."""
from clipper import mix


def clips(*reactions, ctype=None):
    return [{"idx": i, "reaction": r, "content_type": ctype, "score": 85} for i, r in enumerate(reactions)]


def layouts(res):
    return [r["layout"] for r in res]


def test_reaction_level_compares_a_clip_with_the_rest_of_the_video():
    motion = [0, 2, 2, 2, 2, 10, 10, 10, 2, 2, 0, 0]  # a calm video with a burst of movement at 5-7
    assert mix.reaction_level(motion, 5, 7) > 2.5 > mix.reaction_level(motion, 1, 3) > 0.5
    assert mix.reaction_level(None, 0, 10) == 1.0 and mix.reaction_level([0, 0, 0], 0, 2) == 1.0


def test_suitability_decides_and_neighbours_differ():
    res = mix.choose(clips(2.0, 0.3, 1.8, 0.4), None, True)
    assert layouts(res) == ["gta_cam", "gta_hook", "gta_cam", "gta_hook"]
    assert "reacts strongly" in res[0]["reason"] and "action" in res[1]["reason"]


def test_every_layout_is_used_even_when_all_clips_react():
    res = layouts(mix.choose(clips(1.9, 1.9, 1.9), None, True))
    assert set(res) == {"gta_cam", "gta_hook"} and all(a != b for a, b in zip(res, res[1:]))


def test_no_webcam_means_no_framed_cam():
    res = mix.choose(clips(2.0, 1.5), None, False)
    assert layouts(res) == ["gta_hook", "gta_hook"] and "no webcam" in res[0]["reason"]


def test_the_owner_can_restrict_the_mix():
    assert set(layouts(mix.choose(clips(0.2, 2.0, 1.0), ["gta_cam"], True))) == {"gta_cam"}
    assert set(layouts(mix.choose(clips(2.0, 2.0), ["gta_hook"], True))) == {"gta_hook"}
    assert set(layouts(mix.choose(clips(1.0, 1.0), ["bogus"], True))) <= {"gta_cam", "gta_hook"}  # unknown names fall back to the defaults


def test_a_single_clip_gets_its_best_fit_and_talking_content_leans_to_the_cam():
    assert layouts(mix.choose(clips(0.2), None, True)) == ["gta_hook"]
    assert layouts(mix.choose(clips(1.0, ctype="commentary"), None, True)) == ["gta_cam"]
