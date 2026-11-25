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

// The owner's sample: plain prose, as an analyst would write it.
export const EXAMPLE_SYSTEM = {
  name: 'SmartStop: Adaptive Public Transport Assistant, Al Noor City (2032)',
  description: `SmartStop is a city service that helps residents plan bus and tram trips. It is run by the Al Noor City Transport Authority. The cloud platform and the AI model come from a private company, MoveAI Ltd.

Residents use the SmartStop mobile app, or the touch screens at 120 bus stops. To create an account, a resident enters their name, phone number and national ID number, and agrees to share their location. Residents with a disability can upload a medical letter to get free travel and priority seating.

Cameras at each bus stop count how many people are waiting. The cameras also estimate each person's age group and whether they look tired or stressed. These images are sent to the MoveAI cloud, where the AI model studies them.

Every five minutes, the AI model decides which bus routes get extra buses and which routes are reduced. These changes go straight to the bus drivers' screens. No staff member checks them first. On busy days, the model can also stop free travel for some residents to reduce crowding. It does this on its own, and residents get a message in the app.

If the AI model detects a person who looks lost or unwell, it sends an alert to the nearest community support officer. The officer then decides whether to help the person.

Trip history and camera data are stored on the MoveAI cloud for three years. MoveAI may share anonymised trip data with partner companies for advertising.

Staff log in to the control dashboard with a username and password. Two-step login is not used. The Transport Authority reviews the system's decisions once a year.`,
};
