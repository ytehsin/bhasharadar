from .ats import AshbyConnector, GreenhouseConnector, LeverConnector, SmartRecruitersConnector, WorkableConnector
from .feeds import CareerPageConnector, RSSConnector
from .keyed import AdzunaConnector, JoobleConnector, USAJobsConnector
from .public_apis import (
    ArbeitnowConnector,
    HimalayasConnector,
    JobicyConnector,
    RemoteOKConnector,
    RemotiveConnector,
    TheMuseConnector,
)

CONNECTOR_CLASSES = {
    c.meta.kind: c
    for c in [
        RemotiveConnector,
        HimalayasConnector,
        JobicyConnector,
        RemoteOKConnector,
        ArbeitnowConnector,
        TheMuseConnector,
        GreenhouseConnector,
        LeverConnector,
        AshbyConnector,
        WorkableConnector,
        SmartRecruitersConnector,
        AdzunaConnector,
        JoobleConnector,
        USAJobsConnector,
        RSSConnector,
        CareerPageConnector,
    ]
}
