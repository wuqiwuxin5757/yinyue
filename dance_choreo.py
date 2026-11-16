"""Beat-based choreography for the desktop VRM avatar."""
import math
import random
from dataclasses import dataclass


DOWN = (6, 72, 12)
NEUTRAL_ARMS = (DOWN, DOWN)
FEET_TOGETHER = ((0, 0, 0), (0, 0, 0))


@dataclass(frozen=True)
class Key:
    beat: float
    arms: tuple = NEUTRAL_ARMS
    feet: tuple = FEET_TOGETHER  # (lateral, forward, lift), in model metres
    shift: float = 0
    turn: float = 0
    lean: float = 0
    drop: float = 0
    depth: float = 0  # positive values move away from the fixed camera


# Each phrase starts and finishes with the same grounded stance.
# The left foot is on the model's negative X side.
PHRASES = {
    "side_step": (
        Key(0),
        Key(.55, ((4, 18, 24), (18, 52, 68)), ((-.13, 0, .05), (0, 0, 0)), -.05, -12, 7, .026),
        Key(1, ((8, 11, 12), (24, 41, 63)), ((-.29, 0, 0), (0, 0, 0)), -.18, -16, 8, .035),
        Key(1.65, ((24, 44, 72), (6, 18, 15)), ((-.29, 0, 0), (-.16, .03, .055)), -.22, 4, -3, .026),
        Key(2.15, ((25, 43, 65), (5, 10, 14)), ((-.29, 0, 0), (-.17, .03, 0)), -.18, 13, -7, .032),
        Key(2.65, ((8, 43, 38), (8, 29, 22)), ((-.12, .04, .05), (-.17, .03, 0)), -.12, 8, -4, .025),
        Key(3.3, ((8, 60, 22), (15, 46, 44)), ((0, 0, 0), (-.06, 0, .04)), -.04, 2, 1, .02),
        Key(4),
    ),
    "cross_step": (
        Key(0),
        Key(.6, ((15, 65, 64), (6, 20, 24)), ((0, 0, 0), (-.12, .15, .07)), -.04, -22, -5, .031),
        Key(1.10, ((27, 66, 87), (-4, 4, 22)), ((0, 0, 0), (-.20, .18, 0)), -.09, -28, -8, .039),
        Key(1.65, ((-4, 13, 23), (26, 41, 57)), ((.10, -.07, .05), (-.20, .18, 0)), -.035, 15, 7, .029),
        Key(2.2, ((-8, 4, 17), (29, 40, 62)), ((.07, -.09, 0), (-.20, .18, 0)), -.03, 20, 8, .044),
        Key(2.85, ((7, 35, 41), (6, 27, 22)), ((.07, -.09, 0), (-.09, .05, .06)), .015, 8, -4, .026),
        Key(3.45, ((6, 67, 19), (6, 60, 22)), ((.03, -.04, .03), (0, 0, 0)), 0, 0, 0, .018),
        Key(4),
    ),
    "kick_ball_change": (
        Key(0),
        Key(.58, ((-12, 53, 30), (28, 44, 80)), ((0, 0, 0), (.045, .17, .12)), -.03, 17, 6, .032),
        Key(1.03, ((-15, 50, 36), (31, 34, 70)), ((0, 0, 0), (.06, .30, .115)), -.035, 20, 7, .037),
        Key(1.55, ((19, 56, 60), (8, 48, 34)), ((0, 0, 0), (.04, -.10, 0)), -.02, 1, -2, .05),
        Key(2.07, ((30, 31, 71), (-15, 49, 29)), ((-.04, .22, .14), (.04, -.10, 0)), .035, -18, -6, .045),
        Key(2.57, ((24, 43, 69), (-7, 43, 25)), ((-.04, .15, 0), (.04, -.06, .05)), .015, -12, -4, .038),
        Key(3.27, ((12, 55, 38), (13, 47, 41)), ((-.02, .05, .05), (0, 0, 0)), .01, 2, 2, .023),
        Key(4),
    ),
    "diagonal_reach": (
        Key(0),
        Key(.65, ((-4, -48, 24), (27, 54, 87)), ((-.08, 0, .035), (0, 0, 0)), -.05, -15, 8, .026),
        Key(1.2, ((-5, -64, 10), (21, 56, 84)), ((-.17, .05, 0), (0, 0, 0)), -.09, -19, 12, .035),
        Key(1.75, ((10, 20, 70), (14, 14, 54)), ((-.09, .02, .035), (.08, 0, 0)), -.025, -5, 2, .04),
        Key(2.5, ((25, 51, 79), (-5, -56, 14)), ((0, 0, 0), (.19, .035, 0)), .09, 20, -11, .033),
        Key(3.15, ((4, 36, 48), (0, -22, 35)), ((0, 0, 0), (.09, 0, .05)), .03, 11, -6, .02),
        Key(4),
    ),
    "pivot": (
        Key(0),
        Key(.62, ((15, 64, 81), (15, 64, 81)), ((-.08, .05, 0), (.04, -.03, 0)), -.035, -33, 5, .06),
        Key(1.24, ((15, 63, 90), (15, 62, 90)), ((-.07, .03, 0), (.03, .055, .07)), -.025, 75, -5, .037),
        Key(1.92, ((13, 62, 88), (13, 61, 88)), ((-.03, .045, .065), (.06, -.025, 0)), .02, 194, 1, .031),
        Key(2.58, ((0, -20, 22), (1, -16, 18)), ((-.09, .05, 0), (.10, .05, 0)), 0, 330, 3, .047),
        Key(3.3, ((0, 20, 24), (0, 19, 24)), ((-.04, .02, .03), (.06, .01, 0)), 0, 360, 0, .027),
        Key(4, turn=360),
    ),
    "low_groove": (
        Key(0),
        Key(.68, ((21, 63, 92), (23, 69, 85)), ((-.16, 0, 0), (.07, 0, 0)), -.07, -10, 10, .075),
        Key(1.26, ((29, 67, 96), (-6, 14, 29)), ((-.16, 0, 0), (.07, 0, 0)), .035, 12, -10, .045),
        Key(1.8, ((22, 64, 88), (18, 65, 93)), ((-.16, 0, 0), (.07, 0, 0)), -.06, -11, 11, .097),
        Key(2.48, ((-6, 19, 23), (21, 50, 75)), ((-.16, 0, 0), (.07, 0, 0)), .04, 18, -8, .049),
        Key(3.15, ((20, 43, 53), (10, 25, 22)), ((-.055, .02, .045), (.07, 0, 0)), 0, 6, -3, .03),
        Key(4),
    ),
    "accent_pose": (
        Key(0),
        Key(.65, ((11, 15, 51), (27, 23, 68)), ((-.05, .09, .06), (0, 0, 0)), -.025, -15, 5, .026),
        Key(1.20, ((-8, -48, 25), (26, 71, 94)), ((-.11, .12, 0), (.06, 0, 0)), -.035, -23, 10, .054),
        Key(1.85, ((-8, -48, 25), (26, 71, 94)), ((-.11, .12, 0), (.06, 0, 0)), -.035, -23, 10, .054),
        Key(2.45, ((29, 54, 82), (5, 12, 29)), ((-.11, .12, 0), (.15, .02, .05)), .03, 24, -8, .036),
        Key(3.02, ((29, 54, 82), (5, 12, 29)), ((-.03, .04, .05), (.09, .04, 0)), .04, 23, -8, .037),
        Key(4),
    ),
    "back_and_forward": (
        Key(0),
        Key(.55, ((12, 41, 46), (6, 62, 55)), ((-.04, -.15, .065), (0, 0, 0)), -.025, -8, 5, .028, .07),
        Key(1.02, ((17, 50, 67), (15, 34, 72)), ((-.05, -.25, 0), (0, 0, 0)), -.015, -12, 3, .052, .17),
        Key(1.54, ((9, 63, 74), (13, 57, 69)), ((-.05, -.25, 0), (.045, -.19, .06)), .02, -7, -5, .036, .26),
        Key(2.02, ((18, 65, 82), (18, 47, 65)), ((-.05, -.25, 0), (.045, -.30, 0)), .01, 3, -3, .052, .30),
        Key(2.56, ((23, 18, 30), (9, 57, 70)), ((-.045, -.03, .085), (.045, -.30, 0)), -.025, 8, 7, .036, .15),
        Key(3.07, ((9, 52, 56), (10, 26, 29)), ((-.025, .105, 0), (.025, -.09, .06)), .015, 7, -3, .041, -.09),
        Key(3.55, ((6, 64, 22), (6, 63, 27)), ((-.025, .103, .03), (0, 0, 0)), 0, 2, 1, .022, -.04),
        Key(4),
    ),
}


def program(minimum_beats, recorded=False, previous=None):
    order, count = [], 0
    choices = [*PHRASES, "recorded"] if recorded else list(PHRASES)
    while count < minimum_beats:
        choices = random.sample(choices, len(choices))
        first = next(i for i, phrase in enumerate(choices) if phrase != previous and (order or phrase != "recorded"))
        choices[0], choices[first] = choices[first], choices[0]
        for phrase in choices:
            duration = 8 if phrase == "recorded" else 4
            order.append((count, duration, phrase))
            count += duration
            previous = phrase
            if count >= minimum_beats:
                break
    return order


def pose(order, beat, recorded_duration=0.0, strength=1.0):
    start, duration, phrase = next((part for part in reversed(order) if part[0] <= beat), order[0])
    phase = max(0.0, min(duration, beat - start))
    if phrase == "recorded":
        return {"mocap_time": phase / duration * recorded_duration, "mocap_strength": strength,
                "eyes": "open", "mouth": "smile"}
    keys = PHRASES[phrase]
    index = next((i for i, key in enumerate(keys[1:]) if phase <= key.beat), len(keys) - 2)
    a, b = keys[index:index + 2]
    u = (phase - a.beat) / (b.beat - a.beat)
    u = u * u * (3 - 2 * u)
    def lerp(x, y):
        return x * (1 - u) + y * u
    shift = lerp(a.shift, b.shift) * strength
    depth = lerp(a.depth, b.depth) * strength
    turn = lerp(a.turn, b.turn)
    if strength < 1:
        turn = (turn + 180) % 360 - 180
    return {
        "arms": {side: tuple(neutral + (lerp(x, y) - neutral) * strength
                            for x, y, neutral in zip(left, right, DOWN))
                 for side, left, right in zip(("left", "right"), a.arms, b.arms)},
        "dance_feet": tuple((lerp(x, y) * strength - shift, lerp(f, g) * strength + depth, lerp(lift, raise_) * strength)
                            for (x, f, lift), (y, g, raise_) in zip(a.feet, b.feet)),
        "dance_shift": shift, "dance_depth": depth, "walk_drop": lerp(a.drop, b.drop) * strength,
        "spin": turn * strength, "dance_lean": lerp(a.lean, b.lean) * strength,
        "tail": 8 * math.sin(phase * math.pi / 2) * strength,
        "eyes": "wink" if phrase == "accent_pose" and 1.35 < phase < 1.70 else "open",
        "mouth": "smile",
    }

