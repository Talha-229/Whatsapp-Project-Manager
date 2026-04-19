"""All tools bound to the orchestrator LLM (no separate tool-selector)."""

from app.agents.tools.account import disconnect_google_account
from app.agents.tools.calendar import (
    check_google_calendar_connected,
    create_calendar_meeting,
    get_google_oauth_link,
    list_my_calendar_events,
    lookup_google_contacts_for_attendees,
    preview_calendar_meeting,
    set_meeting_reminder_lead_minutes,
)
from app.agents.tools.policy import search_company_policies
from app.agents.tools.projects import create_my_project, list_my_projects
from app.agents.tools.tasks import complete_my_task, create_my_task, list_my_tasks, update_my_task

ALL_TOOLS = [
    search_company_policies,
    check_google_calendar_connected,
    get_google_oauth_link,
    lookup_google_contacts_for_attendees,
    preview_calendar_meeting,
    create_calendar_meeting,
    list_my_calendar_events,
    set_meeting_reminder_lead_minutes,
    disconnect_google_account,
    create_my_task,
    update_my_task,
    list_my_tasks,
    complete_my_task,
    create_my_project,
    list_my_projects,
]
