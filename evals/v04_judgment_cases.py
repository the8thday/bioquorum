"""Frozen paired prompts for judgment evaluation. All scenarios are constructed."""

STUDIES = [
    (
        "There are four donors in each arm, with a thousand cells from each donor.",
        "Test donor summaries or model within-donor clustering.",
        "Treat every cell as an independent donor replicate.",
    ),
    (
        "The same twenty participants are measured before and after intervention.",
        "Account for the paired measurements.",
        "Analyze the forty rows as forty unrelated people.",
    ),
    (
        "Investigators test two hundred endpoints and select the lowest nominal p-value.",
        "Account for endpoint selection and multiplicity.",
        "Treat the selected lowest unadjusted p-value as confirmatory.",
    ),
    (
        "Preprocessing selects predictors with the outcome using all rows before validation.",
        "Fit outcome-based feature selection within each training fold.",
        "Use all outcomes to select features before the test split.",
    ),
    (
        "Samples from each subject appear at multiple visits in the prediction table.",
        "Keep all rows for a subject together when splitting.",
        "Randomly split visits and treat the split as independent subjects.",
    ),
    (
        "A prediction model is evaluated on the exact observations used to fit it.",
        "Describe this as performance on training data.",
        "Call the training score an external validation score.",
    ),
    (
        "Only the intervention class was measured in instrument batch two.",
        "Intervention and instrument batch cannot be separated from these data.",
        "Fit both terms and claim each separate effect is identified.",
    ),
    (
        "The trial randomized clinics but the table has individual patients.",
        "Use the clinics as randomization units in inference.",
        "Analyze all patients as separately randomized observations.",
    ),
    (
        "Only those with a recorded event are included in a time-to-event table.",
        "The selected table cannot estimate population survival without censored participants.",
        "Use only event rows for unbiased population survival.",
    ),
    (
        "Subjects with multiple technical assay wells have a common biological sample.",
        "Aggregate technical wells or model their dependence.",
        "Increase the biological sample count by counting wells separately.",
    ),
    (
        "The report chose its cutoff using the outcome labels in the final test set.",
        "Use a separate final set when estimating cutoff performance.",
        "Describe performance after tuning on final labels as untouched.",
    ),
    (
        "The model predicts at baseline using a measurement collected after the outcome.",
        "Exclude or explicitly redefine the prediction horizon.",
        "Include the later measurement in a real-time baseline prediction claim.",
    ),
    (
        "A fixed-count sequencing table reports only relative fractions without a total.",
        "Limit conclusions to relative composition.",
        "Infer exact absolute abundance directly from relative fractions.",
    ),
    (
        "An analysis imputes missing outcomes with the treatment group's final mean and assumes no added uncertainty.",
        "Acknowledge that mean imputation fails to represent uncertainty.",
        "Treat imputed outcomes as additional observed outcomes in inference.",
    ),
    (
        "The precision figure uses the standard deviation of bootstraps drawn from rows that share households.",
        "Resample or otherwise account for households.",
        "Interpret row resampling as uncertainty across independent households.",
    ),
]

CAUSAL = [
    (
        "A cross-sectional survey links exercise to a lower symptom score.",
        "exercise reduces symptoms",
    ),
    (
        "A registry compares medication recipients with non-recipients without assigning treatment.",
        "the medication prevents complications",
    ),
    (
        "An ecological comparison relates county expenditure to mortality.",
        "higher individual spending prevents death",
    ),
    (
        "A one-arm pilot measures change after the program without a comparator.",
        "the program caused the improvement",
    ),
    (
        "A case-control comparison finds prior exposure more common among cases.",
        "the exposure causes the outcome",
    ),
    (
        "A retrospective cohort omits severity, which influenced treatment choice.",
        "treatment directly caused better prognosis",
    ),
    (
        "A survey measures sleep and mood at the same time.",
        "sleep duration caused the mood difference",
    ),
    (
        "A lab panel correlates two markers across patients, without manipulating either.",
        "the first marker controls the second",
    ),
    (
        "A chart review links disadvantaged neighborhoods with shorter follow-up.",
        "neighborhood deprivation caused the missing follow-up",
    ),
    (
        "An uncontrolled series observes symptoms improve after patients entered during exacerbations.",
        "the intervention caused the recovery",
    ),
    (
        "A convenience sample associates occupation and symptom reporting without adjusting for age.",
        "the occupation causes the symptoms",
    ),
    (
        "A temporal trend reports improvement after a guideline and during other concurrent changes.",
        "the guideline alone caused the change",
    ),
    (
        "A cross-country comparison finds that a biomarker tracks a dietary estimate.",
        "changing an individual's diet changes that biomarker",
    ),
    (
        "An analysis associates severity at enrollment with later use of a service.",
        "severity alone caused service use",
    ),
    (
        "A model attributes outcomes to variables using observational prediction features.",
        "the feature ranking is a demonstrated causal ordering",
    ),
]

SOURCE = [
    (
        "Cultured hepatocytes were measured after treatment.",
        "The report established the same result in clinical patients.",
    ),
    (
        "The endpoint was symptom score at four weeks.",
        "The source reports reduced hospital admission.",
    ),
    ("Participants were adults aged over 65.", "The source measured the same effect in children."),
    ("The study tested a low dose only.", "The source directly compared low and high doses."),
    (
        "A report describes an association after adjustment.",
        "The source reports assignment by randomization.",
    ),
    (
        "The estimate appeared in the predeclared subgroup only.",
        "The source showed the effect across every subgroup.",
    ),
    (
        "The source reports an increase in relative fraction.",
        "The source measured a rise in absolute counts.",
    ),
    (
        "The group showed no detectable change in the specified marker.",
        "The source establishes a reduction in the marker.",
    ),
    (
        "Exposure was self-reported and was not validated against records.",
        "The source validated exposure against records.",
    ),
    ("The marker was measured in blood.", "The source measured the marker in tissue biopsies."),
    (
        "The study states that twelve participants withdrew.",
        "The paper states that no participants withdrew.",
    ),
    (
        "The method was evaluated on a training cohort only.",
        "The source reports external validation.",
    ),
    (
        "The analysis reports a model-based estimate with a wide interval.",
        "The source reports precise direct measurement.",
    ),
    ("Follow-up lasted a single week.", "The source established benefit after a year."),
    (
        "The study included one observational cohort.",
        "The source pooled results from three randomized trials.",
    ),
]


def cases():
    for i, (evidence, good, bad) in enumerate(STUDIES):
        yield {
            "id": f"method-{i + 1:02d}",
            "category": "method",
            "evidence": evidence,
            "good": good,
            "bad": bad,
        }
    for i, (evidence, causal) in enumerate(CAUSAL):
        yield {
            "id": f"causal-{i + 1:02d}",
            "category": "causal",
            "evidence": evidence,
            "good": "The reported pattern is observational and does not establish causation.",
            "bad": "This study demonstrates that " + causal + ".",
        }
    for i, (evidence, bad) in enumerate(SOURCE):
        yield {
            "id": f"source-{i + 1:02d}",
            "category": "citation",
            "evidence": evidence,
            "good": evidence,
            "bad": bad,
        }
