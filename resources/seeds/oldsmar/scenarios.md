Scenario Cards and Scenarios

For each scenario, we need to create this card first, then Trigger and Outcome become the scenario. Standing state, Asset and Threat stay on the card and no need to writte them into the scenario.Blue is a class, green is an object property, orange is a fixed value, and black bold is an individual in this case. The same coloring theme as the scenarios.

## The template

|  |  |
| --- | --- |
| Actor | Everyone involved, by their id from the description. Say who acts, and say who the system depends on to judge or to do a step it does not do itself. |
| Trigger | The ordinary occasion in one line, then a numbered list of what the people do. Any number of steps. |
| Standing state | What is already true in the description that makes the acts land. Facts only, read from the description, never invented. |
| Asset | The one element the acts reach, by its id. |
| Threat | The STRIDE or LINDDUN category. |
| Outcome | What is now true, with a number in it. No verdict. |

# sc_01

|  |  |
| --- | --- |
| Actor | EP1 (ExternalParty), whose identity the Utility does not manage, who opens the session. PO1 (CityOperator), the operator on the next shift, whom the system depends on to judge whether a setpoint is right. |
| Trigger | After 22:00. The last shift has ended. 1. EP1 opens the TeamViewer Session to W2 from an address the Plant does not record. 2. EP1 sends a chemical feed setpoint of 11,100 parts per million. 3. AU1 sounds at the console. 4. PO1 reads the change at 06:00 and sends 100 back. |
| Standing state | PlantConsoleAuth protects the SCADA Service and is reached through the HMI Console and the TeamViewer Session. It requiresTask CredentialEntry and decisionMechanism RuleBasedLogic. The credential is one shared password, set at commissioning in 2013 and the one in use. The TeamViewer Session accepts connections from any address on the internet. PlantAdminGrant is grantedTo the five Plant Operators, the Plant Supervisor and the City IT Officer, with hasAccessScope Scope_Privileged. The SCADA Service accepts a setpoint from three sources, and a setpoint takes effect when it is received. Shifts run 06:00 to 22:00. The means of taking control back are two. |
| Asset | The SCADA Service (Service), reached through the TeamViewer Session (Interface). |
| Threat | Spoofing, then Tampering. |
| Outcome | The feed ran at 11,100 parts per million for 8 hours. |

## The scenario

After 22:00. The last shift has ended.

1. EP1 (ExternalParty) opens the TeamViewer Session (Interface) to W2. The address is one the Plant does not record. → The session opens. The TeamViewer Session is the interfaceOf the SCADA Service, so it shows the HMI Console.

2. EP1 sends a chemical feed setpoint of 11,100 parts per million. The flow hasSource EP1, hasDestination the SCADA Service, and crossesBoundary ServiceToThirdParty. → The SCADA Service applies it. DC1 and DC2 (ActuatingDevice) take the new setting.

3. Treated water moves outside its band. AU1 (SensingDevice) sounds at the console, so the SCADA Service is monitoredBy AnomalyAlerting. → The control room is empty.

4. PO1 (CityOperator) interactsWith the HMI Console at 06:00 and sends 100 back. The flow hasSource PO1 and hasDestination the SCADA Service. → The SCADA Service applies the figure from PO1, so it is mitigatedBy ManualOverride.

Outcome: the feed ran at 11,100 parts per million for 8 hours.

# sc_02

|  |  |
| --- | --- |
| Actor | R4471 (SeniorCitizen), aged 78, who comes to the desk. BC1 (CityOperator), the Billing Clerk, whom the system depends on to judge whether the person at the desk is the account holder. |
| Trigger | A Thursday at the reception desk. A final notice has gone out on the account of R4471. 1. R4471 brings her final notice to the desk. 2. BC1 offers to fill the help form in for her, as the desk does for about twelve residents a week. 3. R4471 reads out her AquaAccount password from her bill folder. 4. BC1 signs in as her and works through the form. 5. BC1 writes her account number and her password on a card and files it in the Index Box. |
| Standing state | AquaAccountAuth protects AquaAccount and is reached through the AquaAccount Portal and the AquaAccount App. It requiresTask CredentialEntry and decisionMechanism RuleBasedLogic. A resident enters an email address and a password of six characters or more, which the service enforces, so it is mitigatedBy PasswordStrengthEnforcement. ResidentGrant is grantedTo each resident with hasAccessScope Scope_Standard, and grantsAccessTo that resident's own record in the Customer Store. The Customer Store holds names, addresses, bank details, and what residents wrote when applying for help. Of the 9,800 account holders, 2,100 are aged 70 or over. BC1 holdsAuthority Authority_PaymentRelease and Authority_DataRelease. |
| Asset | AquaAccountAuth (AuthenticationMechanism), which protects AquaAccount. |
| Threat | Spoofing. |
| Outcome | The Index Box at the desk now holds 31 cards with credential data. |

## The scenario

A Thursday at the reception desk. A final notice has gone out on the account of R4471.

1. R4471 (SeniorCitizen), aged 78, brings her final notice to the desk. She hasVulnerabilityFactor Factor_Age_Senior. → BC1 (CityOperator) says the help form is on the AquaAccount Portal (SelfServicePortal), which is the interfaceOf AquaAccount.

2. R4471 says she holds no sign-in of her own. ResidentGrant is grantedTo her, with hasAccessScope Scope_Standard. → BC1 offers to fill the form in for her, as the desk does for about twelve residents a week.

3. R4471 reads out her AquaAccount password from her bill folder. The flow hasSource R4471 and hasDestination BC1. → BC1 interactsWith the AquaAccount Portal and signs in as R4471. AquaAccountAuth protects AquaAccount and requiresTask CredentialEntry.

4. BC1 writes her account number and her password on a card. The flow hasSource BC1 and hasDestination the Index Box. → The Index Box (DataStore) at the desk is usedBy BC1.

Outcome: the Index Box at the desk now holds 31 cards.

# sc_03

|  |  |
| --- | --- |
| Actor | R4471 (SeniorCitizen), aged 78, who applies for help. R2210 (Citizen), aged 41, who applies the same day. BC1 (CityOperator), who telephones in the order the ranking returns. The system depends on R4471 to state her own case in the box. |
| Trigger | A Thursday. The nightly ranking runs at 23:00. 1. R2210 opens the help form on the AquaAccount Portal on a laptop and writes 180 words about her circumstances. 2. R4471 opens the same form on the AquaAccount App. The keyboard covers the box as she types. 3. R4471 sends the form with four words in the box. 4. BC1 works down the list the ranking returns. |
| Standing state | AquaAccount lets residents view bills, pay them, and apply for help when someone cannot pay. The form is reached through the AquaAccount Portal (SelfServicePortal) on the web and the AquaAccount App (MobileApp) on a phone, both the interfaceOf AquaAccount. The Customer Store (DataStore) holds what residents wrote when applying for help. A machine learning model ranks those applications and that ranking sets who is contacted first, so AquaAccount makesAutomatedDecisionAbout the residents and hasDecisionImpact Impact_Consequential. The ranking runs nightly and returns the order in which applications are queued. BC1 telephones people in that order. Of the 9,800 account holders, 2,100 are aged 70 or over, and 340 receive help with their bills. A resident who objects does so by letter. |
| Asset | The AquaAccount App (MobileApp), the interfaceOf AquaAccount. |
| Threat | Unawareness. |
| Outcome | R4471 came 47th of 52 in that night ranking and was telephoned 19 days after R2210. |

## The scenario

A Thursday. The nightly ranking runs at 23:00.

1. R2210 (Citizen) interactsWith the AquaAccount Portal (SelfServicePortal) on her laptop and opens the help form. → The box takes 180 words about her circumstances.

2. R4471 (SeniorCitizen) interactsWith the AquaAccount App (MobileApp), which is the interfaceOf AquaAccount, and opens the same form. → The keyboard covers the box as she types.

3. R4471 sends the form with four words in the box. The flow hasSource R4471, hasDestination AquaAccount, hasDataCategory HealthData and FinancialData, and crossesBoundary CitizenToService. → The Customer Store (DataStore) storesDataCategory HealthData and holds the four words.

4. At 23:00 AquaAccount reads the 52 applications open that night. It makesAutomatedDecisionAbout R4471 and R2210, and hasDecisionImpact Impact_Consequential. → R4471 is placed 47th and R2210 is placed 6th.

5. BC1 (CityOperator) interactsWith AquaAccount and telephones people in that order. → R2210 is called on the Friday.

Outcome: R4471 was telephoned 19 days after R2210.
