export const OUTLINE = [
  'Purpose and scope',
  'Elements',
  'People and organisations',
  'Data flows',
  'Settings and procedures in force',
  'What is not in place',
]
  .map((heading, index) => `${index + 1}. ${heading}\n\n`)
  .join('\n')
  .trimEnd();

// The owner's samples: plain prose, as an analyst would write it.
const SMARTSTOP = {
  name: 'SmartStop: Adaptive Public Transport Assistant, Al Noor City (2032)',
  description: `SmartStop is a city service that helps residents plan bus and tram trips. It is run by the Al Noor City Transport Authority. The cloud platform and the AI model come from a private company, MoveAI Ltd.

Residents use the SmartStop mobile app, or the touch screens at 120 bus stops. To create an account, a resident enters their name, phone number and national ID number, and agrees to share their location. Residents with a disability can upload a medical letter to get free travel and priority seating.

Cameras at each bus stop count how many people are waiting. The cameras also estimate each person's age group and whether they look tired or stressed. These images are sent to the MoveAI cloud, where the AI model studies them.

Every five minutes, the AI model decides which bus routes get extra buses and which routes are reduced. These changes go straight to the bus drivers' screens. No staff member checks them first. On busy days, the model can also stop free travel for some residents to reduce crowding. It does this on its own, and residents get a message in the app.

If the AI model detects a person who looks lost or unwell, it sends an alert to the nearest community support officer. The officer then decides whether to help the person.

Trip history and camera data are stored on the MoveAI cloud for three years. MoveAI may share anonymised trip data with partner companies for advertising.

Staff log in to the control dashboard with a username and password. Two-step login is not used. The Transport Authority reviews the system's decisions once a year.`,
};

const CAREPATH = {
  name: 'CarePath AI: Remote Patient Monitoring System, Al Noor City (2032)',
  description: `CarePath AI is a healthcare service operated by Al Noor City Health Network. It helps clinicians monitor patients with chronic conditions after they leave hospital. The monitoring platform and AI model are provided by a private company, MedSignal Ltd.

Patients use the CarePath mobile app and a connected home-monitoring kit. The kit includes a blood-pressure monitor and pulse oximeter. Patients enter readings when prompted; the devices also send readings automatically. To register, patients provide their name, date of birth, contact details, health ID and information about their condition. They can also choose to share location data with the service.

The platform combines these readings with patients’ medication lists and recent clinical records. The AI model analyses the data to estimate whether a patient’s condition may be worsening. It assigns each patient a low, medium or high monitoring priority and updates that priority every 15 minutes.

High-priority alerts are sent to the nurse dashboard. A nurse reviews the alert and decides whether to contact the patient, arrange an appointment or take another action. The AI does not diagnose patients or change their treatment. If a patient does not submit readings for 24 hours, the system sends a reminder through the app.

Patients can send messages to their care team through the app. Nurses and doctors access the dashboard using individual accounts and passwords. Doctors can view records for patients under their care; nurses can view records assigned to their monitoring team. Multi-factor authentication is not used. The Health Network reviews staff access twice a year.

Patient readings, messages and alert histories are stored in MedSignal’s cloud platform for five years after a patient leaves the programme. MedSignal provides technical support and may use de-identified data to improve its service. The Health Network can export patient records for clinical audit.`,
};

// Offered on the System step, by their short names.
export const EXAMPLES = [
  { label: 'SmartStop', ...SMARTSTOP },
  { label: 'CarePath AI', ...CAREPATH },
];
