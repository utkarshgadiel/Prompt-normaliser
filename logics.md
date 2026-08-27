# Logic Definitions
## Total Lead
To count the total lead count the number of rows, if asked date wise leads, first apply filter on created date and then count the number of rows that will be the total lead count.

## Valid Lead
A lead is considered valid when the customer_feedback_c column does not equal "junk". Every lead that has any feedback value other than junk is counted as a valid lead.

## Junk Lead
A lead is considered junk when the customer_feedback_c column equals "junk". These are leads that have been marked as irrelevant or invalid.

## Open Lead
A lead is considered open when the customer_feedback_c column equals "Discussion Pending". These are leads where a conversation or follow-up is yet to happen.

## New Lead
A lead is considered new when the status column equals "new". The total count of all rows matching this condition gives the new lead count.

## Qualified Lead
A lead is considered qualified when the customer_feedback_c column equals "interested". These leads have shown interest and are moved forward in the funnel.

## Unqualified Lead
A lead is considered unqualified when the status column equals "unqualified". These leads have been reviewed and determined to not be a fit.

## Cold Lead
A lead is considered cold when the rating_c column equals "cold". These leads have low engagement or interest.

## Hot Lead
A lead is considered hot when the rating_c column equals "hot". These leads have high engagement and are close to conversion.

## Warm Lead
A lead is considered warm when the rating_c column equals "warm". These leads are moderately engaged and need further nurturing.

## Nurturing Lead
A lead is under nurturing when the status column equals "nurturing". The total count of all rows matching this condition gives the nurturing lead count.

## By Source
Leads are grouped by the lead_source_c column. Each unique value in this column represents a different lead source such as Digital, Referral, Channel Partner, etc.

## Project Wise Lead
Leads are grouped by the project_c column. Each unique value in this column represents a different project, giving a breakdown of how many leads belong to each project.

## Sub-Source Lead
Leads are grouped by the lead_source_sub_category_c column. This gives a deeper breakdown within each source, identifying the specific sub-channel that brought in the lead.

## Product Wise Lead
Leads are grouped by the product_c column. Each unique value in this column represents a product, giving a breakdown of leads per product.

## Year on Year (Date Queries)
All date-based filtering and analysis uses the created_date_c column. This applies to any query involving a specific date, date range, month, quarter, or financial year comparison such as MOM, QOQ, or YOY.

## Junk Reason
The junk_reason_c column is used to identify why a lead was marked as junk. Grouping by this column shows the distribution of junk reasons across all junk leads.

## Disqualification Reason
The disqualification_reason_c column is used to identify why a lead was disqualified. Grouping by this column shows the distribution of disqualification reasons across all unqualified leads.

## Owner / Customer / Agent Wise Lead
Leads are grouped by the OwnerName_c column. This gives a breakdown of leads assigned to each owner, agent, or customer relationship manager.
## Sales Done
Sales done is calculated from the opportunity report table. The sales_order_number_c column is used for this. Any row where sales_order_number_c is not null and not empty is counted as a completed sale. The total count of such rows gives the sales done count.

## Opportunity Count
The opportunity count is simply the total row count from the opportunity report table. Every row in the opportunity table represents one opportunity, so the total number of rows is the opportunity count.

## Total Task
The total task count is the total number of rows in the task report table. Every row in the task table represents one task, so the total row count is the total task count.

## Follow Up Task
Follow up tasks are identified from the task report using the subject_c column. Any row where subject_c matches any of the following values is counted as a follow up task: "Follow Up", "Sales Follow Up", "Experience Calling Follow Up", "Welcome Calling Follow Up". When the user asks for "follow up", "followup", or "follow-up", Python expands this to all four values using an IN condition.

## Task Status Based Filters
Tasks are filtered using the status_c column in the task report. The following status mappings apply:

- Completed Task: status_c = "Completed"
- Open Task: status_c = "Open"
- In Progress Task: status_c = "In Progress"
- Cancelled Task: status_c IN ("Cancelled", "Canceled", "Cancel") — triggered by keywords "cancelled", "canceled", or "cancel"
- Deferred Task: status_c = "Deferred"
- Closed Task: status_c = "Closed"

## Task Feedback Based Filters
Tasks are filtered using the sales_team_feedback_c column in the task report. The following mappings apply:

- Qualified Task: sales_team_feedback_c = "Qualified"
- Disqualified Task: sales_team_feedback_c = "Disqualified"

## Task Subject Based Filters
Tasks are filtered using the subject_c column in the task report. The following subject mappings apply:

- Follow Up / Followup / Follow-up: subject_c IN ("Follow Up", "Sales Follow Up", "Experience Calling Follow Up", "Welcome Calling Follow Up")
- Sales Follow Up: subject_c = "Sales Follow Up"
- Re-query / Requery: subject_c = "Re-query requested"
- Tried Calling: subject_c expands to all tried-calling related values
- Click to Call / Click Call: subject_c expands to all click-to-call related values
- Call Back By Sales Expert: subject_c = "Call Back By Sales Expert"
- Experience Calling Follow Up: subject_c = "Experience Calling Follow Up"
- Welcome Calling Follow Up: subject_c = "Welcome Calling Follow Up"
- Request Call Back / Request Callback: subject_c = "Request call back"
- Live Chat / Live Chat Query: subject_c = "Live Chat Query"

## Total Cases / Service Requests (SR)
The total cases or total service requests count is the total number of rows in the service request table. Every row in the service request table represents one case or SR, so the total row count is the total cases count.
## Event / Appointment Status Based Filters
All event and appointment filters are applied on the event report table using two columns together: subject_c and appointment_status_c.

### Meeting Done / Completed Meeting
A meeting is counted as done or completed when subject_c equals "Personal Appointment Booked" and appointment_status_c equals "completed". This condition applies for keywords like "completed meeting", "completed meetings", "completed appointment", "meeting completed", "total meeting", and "total meetings".

### Meeting Booked
A meeting is counted as booked when subject_c equals "Personal Appointment Booked". No appointment_status_c filter is applied here. The total count of all such rows gives the meeting booked count.

### Scheduled Appointment / Meeting
A meeting is counted as scheduled when appointment_status_c equals "scheduled". This applies for keywords like "scheduled appointment", "scheduled appointments", "scheduled meeting", and "scheduled meetings".

### Cancelled Appointment / Meeting
A meeting is counted as cancelled when appointment_status_c equals "cancelled". This applies for keywords like "cancelled appointment", "cancelled appointments", "cancelled meeting", and "cancelled meetings".

### Revisit
A meeting is counted as a revisit when appointment_status_c equals "revisit" or "re-visit". This applies for keywords like "revisit", "re-visit", "revisit appointment", and "revisit meeting".

### Rescheduled
A meeting is counted as rescheduled when appointment_status_c equals "rescheduled" or "re-schedule". This applies for keywords like "rescheduled", "re-schedule", "rescheduled appointment", and "rescheduled meeting".

## Lead Funnel Conversion Logic

The lead funnel is computed in the following sequence. Each metric feeds into the next stage of the funnel, and the conversion ratios show how many leads move from one stage to the next.

Total Leads
The total number of all leads in the selected date range. This is the starting point of the funnel.

Valid Leads
Filter on customer_feedback_c where the value is not equal to junk. The count of remaining rows is the valid lead count.

Junk Leads
Filter on customer_feedback_c where the value equals junk. The count of such rows is the junk lead count.

SOL Leads (Interested)
Filter on customer_feedback_c where the value equals interested. The count of such rows is the qualified or SOL lead count.

Meeting Booked
From the event report, filter on subject_c where the value equals "Personal Appointment Booked". The count of such rows is the meeting booked count.

Meeting Done
From the event report, filter on subject_c equals "Personal Appointment Booked" and appointment_status_c equals "completed". The count of such rows is the meeting done count.

Sales Done
From the opportunity report, filter on sales_order_number_c where the value is not null and not empty. The count of such rows is the sales done count.

Junk %
Calculated as (Junk Leads / Total Leads) * 100. This gives the percentage of junk leads out of all total leads.

TL:VL — Total Leads to Valid Leads
Ratio of Total Leads divided by Valid Leads. Shows how many total leads it takes to get one valid lead.

VL:SOL — Valid Leads to SOL Leads
Ratio of Valid Leads divided by SOL Leads. Shows how many valid leads it takes to get one interested or qualified lead.

SOL:MB — SOL Leads to Meeting Booked
Ratio of SOL Leads divided by Meeting Booked. Shows how many interested leads it takes to get one meeting booked.

MB:MD — Meeting Booked to Meeting Done
Ratio of Meeting Booked divided by Meeting Done. Shows how many booked meetings it takes to get one completed meeting.

MD:SD — Meeting Done to Sales Done
Ratio of Meeting Done divided by Sales Done. Shows how many completed meetings it takes to close one sale.

TL:SD — Total Leads to Sales Done
Ratio of Total Leads divided by Sales Done. Shows the overall funnel efficiency from the very top to a closed sale.

VL:SD — Valid Leads to Sales Done
Ratio of Valid Leads divided by Sales Done. Shows how many valid leads it takes to close one sale.

SOL:SD — SOL Leads to Sales Done
Ratio of SOL Leads divided by Sales Done. Shows how many interested leads it takes to close one sale.

MB:SD — Meeting Booked to Sales Done
Ratio of Meeting Booked divided by Sales Done. Shows how many booked meetings it takes to close one sale.

