from bioquorum.core.refs import NamedValue
from bioquorum.review.recompute import measurement_statement


def test_only_a_measured_quantity_can_be_closed_by_rebinding_its_value():
    named = [NamedValue(name="control_mean", value=3, unit="AU")]
    assert measurement_statement("Control mean was {{control_mean}} AU.", named)
    assert measurement_statement("control_mean = {{control_mean}} AU", named)
    assert not measurement_statement("The higher control mean was {{control_mean}} AU.", named)
    assert not measurement_statement(
        "Control mean was {{control_mean}} AU, below the threshold.", named
    )
    assert not measurement_statement("Control mean was {{control_mean}} mg.", named)
