from bioquorum.core.refs import NamedValue
from bioquorum.core.status import StatusTrigger, VerificationStatus, transition
from bioquorum.core.verification import Tolerance, compare_values


def test_unit_changes_and_missing_values_are_not_matches():
    original = [NamedValue(name="extent", value=2.0, unit="mm")]
    assert compare_values(original, [], Tolerance())[0].matched is None
    changed = [NamedValue(name="extent", value=2.0, unit="cm")]
    assert compare_values(original, changed, Tolerance())[0].matched is False


def test_relative_comparison_and_status_ladder():
    original = [NamedValue(name="rate", value=10.0)]
    revised = [NamedValue(name="rate", value=10.5)]
    assert compare_values(original, revised, Tolerance(relative=0.05))[0].matched
    assert not compare_values(original, revised, Tolerance(relative=0.01))[0].matched
    trigger = StatusTrigger.REPLICATION_MATCHED
    assert transition(VerificationStatus.REVIEWED, trigger) == VerificationStatus.REVIEWED
    assert transition(VerificationStatus.REPRODUCED, trigger) == VerificationStatus.REPLICATED
