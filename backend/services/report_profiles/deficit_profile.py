"""Legacy name: insights now come from YAML ReportEngine."""
from services.profile_registry import get_profile
from services.report_engine import ReportEngine
from services.report_profiles.base_profile import ReportProfile


class DeficitProfile(ReportProfile):
    def get_insights(self, df):
        config = get_profile("deficit_report")
        if config is None:
            return []
        return ReportEngine(config).get_insights(df)

    def get_dashboard_spec(self, df):
        from services.generic_dashboard import build_generic_spec
        return build_generic_spec(df)
