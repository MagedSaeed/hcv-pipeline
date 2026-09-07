Ontology Vocabulary (classes, properties and fixed values)

This is the word list for writing system descriptions and scenarios and here it explains how to use it: everything named in a system description must be an instance of a class in section 1. Every person must be an Actor. Every sentence that joins two things must correspond to an object property in section 2. Every setting, level or kind must be one of the fixed values in section 3. Every fact stated as a number or as a yes or no, rather than as a link to another named thing, must use a datatype property in section 4. If a word you want to write has no term here, either pick the term that already covers it, or the term is missing you can use the needed term and provide them as a list of the bottom of the case, and the ontology needs extending.

# 1. Classes

## 1.1 SystemElement (55)

The things a system is made of. Every noun in a system description that is a device, service, store, interface, network, grant, flow, place or record must be one of these.

| Class | What it means |
| --- | --- |
| AccessGrant | A granted access right. |
| ActiveUserTask | A task in which the person must perform a physical or perceptual action correctly for a sensor to read. Every member is a learned-model capture. A task decided by rule-based logic, such as entering a credential, is not one of these. This class carries the AI-based requirement. |
| Asset | A system element that can be exposed to a threat. Elements outside this class, namely interfaces, authentication mechanisms, access grants and physical entry points, hold the enabling state; the exposure rules transfer the threat to the asset behind them. |
| AuthenticationMechanism | A mechanism that verifies the identity of a person for a service. |
| AuthenticationTask | A task an authentication mechanism requires a person to perform. |
| BehaviouralSignalCheck | A check of background behavioural signals, with no user action. |
| CredentialEntry | Entry of a human-chosen secret. Decided by rule-based logic and not by a learned model, and therefore a sibling of the active and passive user tasks rather than a member of either. |
| Data | Module grouping the data-related elements of a described city: data in motion, data at rest, and the categories used to describe both. |
| DataCategory | A type of data exchanged or stored, such as health, location or biometric data. An open vocabulary: further categories may be added if needed for more cases |
| DataFlow | Movement of data between system elements or parties. |
| DataStore | A storage component holding city data. |
| Device | A physical computing device in the city system. |
| DeviceTokenCheck | A check of a token held by the person's device, with no user action. |
| EmotionRecognitionService | An AI service that infers emotional state from citizen input such as facial image, voice or text. |
| HighRiskFlow | A high sensitivity flow that also crosses a trust boundary. Defined so that every such flow in a described city can be listed in one query. |
| HighSensitivityDataCategory | Inferred. Data categories assessed as carrying high sensitivity. Health data and biometric data processed for identification are special categories of personal data. |
| HighSensitivityFlow | A data flow carrying at least one high sensitivity data category, wherever it goes. Defined so that every such flow in a described city can be listed in one query. |
| Interface | An interaction surface of a service or a device. |
| IoTDevice | A device operating without a person present once installed. |
| MobileApp | A smartphone or tablet application through which a citizen reaches a city service. Distinguished from a self-service portal on two axes. On operability, reduced screen area, small touch targets and dependence on platform accessibility settings intensify the participatory interface condition. On privacy, the application requests device permissions such as location, camera, microphone and contacts, so the dominant concerns are permission scope and disclosure rather than operability alone. |
| ParticipationService | A service through which citizens take part in a collective decision, such as participatory budgeting, consultation or petition. |
| PassiveBiometricCapture | A biometric sample taken without the person performing a step. |
| PhysicalEntryPoint | A door, gate or barrier controlling entry to an area. |
| PrivacySettingsPanel | A user-configurable privacy settings surface. a panel within a mobile application or self-service portal |
| RestrictedArea | A physical area to which access is restricted. |
| Service | A city system component providing a service. Subtypes are distinguished by the kind of demand the service places on a person. |
| SharedEndpoint | An endpoint used by many people, where responsibility for its state rests with no single user. Examples include public service kiosks and library terminals. |
| SystemElement | Any part of a described city system that can carry a control. Domain of mitigatedBy and monitoredBy. Persons are deliberately excluded: they are modelled under Actor and can therefore never carry a control or be assigned a threat. |
| TrustBoundary | A boundary between zones of differing trust. |
| UserEndpoint | A device a person operates directly. Subtypes are distinguished by who holds responsibility for the state of the device. |
| UserInputFlow | A data flow whose origin is a person. Inferred from the recorded source, never typed by hand. |
| VulnerabilityCondition | A system element that sits in a state in which a human-centric vulnerability is enabled. Every subclass is defined, and every subclass is also asserted under the element type it refines, so the element and the state it is in are both visible in the hierarchy. |
| WebAccessChannel | A browser route through which users reach a service: the address or domain name a person types, clicks or scans. Concerns how the user arrives, not what the user does after arriving. Boundary: this class is the door, SelfServicePortal is the room behind it. |

## 1.2 Actor (9)

The people and organisations. Every role in a description or a scenario must be one of these.

| Class | What it means |
| --- | --- |
| Actor | A person or organisation involved in the planning, operation or use of a city system. Deliberately not a SystemElement: the model describes what a system asks of a person, never a failing of the person. |
| Child | A person under the age of majority, who cannot give meaningful consent. |
| Citizen | An individual who interacts with city infrastructure and services as a recipient of those services, Not inherently vulnerable; vulnerability depends on subtype. |
| CityOperator | A trained member of city staff operating city systems. Also the insider role in the unconstrained insider access condition. |
| DecisionMaker | A person who acts on the output of a city system to take or approve a decision affecting others. A kind of user, not a value list. |
| ExternalParty | A party outside the city system whose identity the system does not manage. The absence of managed identity, rather than organisational status, is what distinguishes this class from ThirdPartyProvider. |
| SeniorCitizen | An older citizen, for whom reduced sensory, motor or cognitive capability may be assumed for the purposes of accessibility assessment. |
| User | A natural person interacting with city systems. |
| VulnerableUser | A person who carries at least one recorded vulnerability factor. Any kind of user may be vulnerable, including a city operator, and a person may carry several factors at once. |

## 1.3 Qualifier (15)

These are not things, they are settings, levels and kinds, it used for ranking levels.

| Class | What it means |
| --- | --- |
| AccessScope | Scope of an access grant, assessed against operational need. |
| Authority | Something a person is permitted to decide or cause, independent of any system access they may also hold. A person may hold more than one at the same time. System administration is not a member, because that case is carried by an access grant of privileged scope. |
| AutomationLevel | Degree of automation of a service. |
| ConsequenceType | The kind of harm following compromise of an asset. Distinct from DecisionImpact, which concerns the effect of an automated decision on a person. |
| ControlDomain | The four main governance domains. A domain groups control families; a control type names one family. |
| DataSensitivityLevel | A value list ranking how much harm the disclosure of a data category would cause. |
| DecisionImpact | Whether an automated decision has consequential effect on a person. A value list of how far a decision reaches, not a person. |
| DecisionMechanism | How an authentication mechanism reaches a decision: by a learned model or by fixed rule-based logic. A value list of ways a decision is reached, not a person. |
| DeviceConfigState | A value list saying whether a device still runs the configuration it shipped with. |
| LifecyclePhase | The five technology lifecycle phases. Used to record the phase in which the enabling state of a condition originates, and therefore the phase at which it can be prevented. |
| PermissionScope | Whether the device permissions held by an application are limited to those required by its service function. Assessed against declared purpose, consistent with the data minimisation principle in Article 5(1)(c) of Regulation (EU) 2016/679. |
| PrivacyConfigType | Privacy_Default denotes shipped vendor settings untouched by the user, which is the state the permissive default privacy condition detects. Privacy_Custom denotes settings adjusted by the user. Privacy_ByDefault denotes protective settings applied from the outset, in the sense of Article 25 of Regulation (EU) 2016/679. |
| Qualifier | Parent of the closed vocabularies used to describe system elements. A qualifier answers what kind or how much, never what a thing is, and its members are values rather than entities. |
| TechnicalFunction | The four technical functions of the reference technical architecture. |
| VulnerabilityFactor | A recorded reason why a person is more exposed to harm when using a city system. A person may carry more than one factor at the same time. |

## 1.4 ControlType (6)

The families of control. A control is named to say that a protection exists.

| Class | What it means |
| --- | --- |
| CaptureAccessibilityControl | Control families that let a person complete an active capture task themselves, either because the capture is made accessible or because an equivalent route is offered that they can complete without help. Blocking set for the limited authentication involvement condition. Completion by another person on their behalf is not a member, because the check then reads the other person. |
| ContactVerificationControl | Control families that establish the identity of an external party or remove the need for the user to establish it. Blocking set for the unverified contact channel condition. |
| ControlType | The catalogue of control families. A family is present or absent on a system element; the model records presence of a family rather than the number of deployments of it. Blocking families attach through mitigatedBy and remove the enabling state. Detecting and exposure-reducing families attach through monitoredBy and leave the enabling state in place. The catalogue grows only when a taxonomy row first requires a family. |
| DataLifecycleControl | Control families that bound the lifetime of stored data. Blocking set for the unmanaged disposal condition. |
| InterfaceAccessibilityControl | Control families that make a participatory interface operable by users with reduced sensory, motor or cognitive capability. Blocking set for the poor participatory interface condition. |
| PasswordControl | Control families that reduce dependence on the strength of a human-chosen secret. Blocking set for the unprotected password authentication condition. |

## 1.5 ThreatCategory (3)

| Class | What it means |
| --- | --- |
| LINDDUNCategory | A value list of the seven LINDDUN privacy threat categories. Kept apart from the STRIDE security categories. |
| STRIDECategory | A value list of the six STRIDE security threat categories. Kept apart from the LINDDUN privacy categories. |
| ThreatCategory | Superclass of the STRIDE security categories and the LINDDUN privacy categories. The two frameworks are never conflated: one condition may enable several categories, but each individual belongs to exactly one framework. |

# 2. Object properties

Read each as relationship between two things. Sometimes you need to read the whole pack to identify it.

| Property | Domain → Range | What it means |
| --- | --- | --- |
| admissionBy | PhysicalEntryPoint → User | The person whose admission action governs entry through the point. |
| categorySensitivity | DataCategory → DataSensitivityLevel | The sensitivity level assessed for a category of data. Each assessment is recorded as a single assertion so that it can be justified individually. |
| configuredBy | SystemElement → User | The person to whom the security configuration of an asset is delegated. |
| controlsEntryTo | PhysicalEntryPoint → RestrictedArea | The area a person passes into through this entry point. |
| crossesBoundary | DataFlow → TrustBoundary | A trust boundary the data flow passes through. |
| decisionMechanism | AuthenticationMechanism → DecisionMechanism | How the authentication mechanism reaches its decision, by learned model or by rule based logic. |
| enablesThreatCategory | any → ThreatCategory | A threat category that the element is exposed to because of an enabling state detected on it or on an element connected to it. Asserted by the exposure rules, never coded by hand. |
| grantedBy | AccessGrant → User | The person whose judgment granted the access right. |
| grantedTo | AccessGrant → User | The person who holds the access this grant gives. |
| grantsAccessTo | AccessGrant → SystemElement | The asset reached through the access right. |
| hasAccessScope | AccessGrant → AccessScope | How much the access grant gives, ordinary or privileged. |
| hasAutomationLevel | Service → AutomationLevel | How much of the service's work runs without a person acting. |
| hasConfigState | Device → DeviceConfigState | Whether the device still runs the configuration it shipped with. |
| hasDataCategory | DataFlow → DataCategory | A category of data the flow carries. |
| hasDecisionImpact | Service → DecisionImpact | How far a decision taken by the service reaches for the person it is about. |
| hasDestination | DataFlow → any (inverse of receivesFlow) | The element or party the data flow arrives at. |
| hasInterface | Service → Interface (inverse of interfaceOf) | A surface through which people reach this service. |
| hasPermissionScope | Interface → PermissionScope | Whether the device permissions held by an application are limited to those its service function requires. Assessed against declared purpose, following the same pattern as access scope on a grant. |
| hasPrivacyConfig | Service → PrivacyConfigType | The privacy configuration the service is currently running on. |
| hasSource | DataFlow → any | The element or party the data flow originates from. |
| hasVulnerabilityFactor | User → VulnerabilityFactor | A reason why the person is more exposed to harm when using a city system. |
| holdsAuthority | User → Authority | Something the person is permitted to decide or cause. An authority is held by a person and points at no system element, which distinguishes it from an access grant: a person may hold an authority with no system access, and may hold system access carrying no authority. |
| holdsGrant | User → AccessGrant (inverse of grantedTo) | An access grant this person holds. Reads the grant from the person's side. |
| interactsWith | User → SystemElement (inverse of usedBy) | An element the person uses. |
| interfaceOf | Interface → Service | The service this surface gives people access to. |
| makesAutomatedDecisionAbout | Service → User | A person about whom the service decides with no person in the loop. |
| mitigatedBy | SystemElement → ControlType | A control that removes the enabling state on this element. Only this property is read by a condition definition, and only this property is closed when a case is coded. A judgment that a control is absent is recorded on the individual, either as a negative property assertion naming the control, or, where a condition tests a control family, as a maximum cardinality of zero on that family. Both forms leave the controls that are present untouched. |
| monitoredBy | SystemElement → ControlType | A governance control that watches or assures this element without removing an enabling state, for example an audit, a risk assessment or a role assignment. Recorded for reporting. No condition definition reads it, because assurance does not close a vulnerability. |
| operatedBy | Asset → User (inverse of operates) | The person responsible for running or overseeing this element. |
| operates | User → Asset | An element the person is responsible for running or overseeing. |
| permitsContactWith | Service → ExternalParty | A party outside the city that the service lets a user reach. |
| protects | AuthenticationMechanism → SystemElement | An element whose access this authentication mechanism guards. |
| receivesFlow | any → DataFlow | A data flow that arrives at this element or party. |
| requiresTask | AuthenticationMechanism → AuthenticationTask | A task the mechanism requires a person to perform. |
| storesDataCategory | DataStore → DataCategory | A category of data held in this store. |
| usedBy | SystemElement → User | The person who uses this element. |

# 3. Fixed values

Named individuals. Where a property points to one of these types, the value must be taken from this list and no other.

## ControlType (43)

| Value | What it means |
| --- | --- |
| AccessibleCapture | A capture step designed so a person with limited ability can complete it. |
| AccessibleInterfaceDesign | An interface designed so a person with limited ability can use it. |
| ActiveMonitoringTask | A named task in the workflow requiring a person to watch the system while it runs. |
| AnomalyAlerting | An automatic warning when the system behaves unusually. |
| AntiTailgatingBarrier | A physical barrier letting only one person through for each admission. |
| AuthoritativeSupportChannel | A single published route people are told to use, so an unexpected approach can be checked against it. |
| AwarenessPreparation | Training and briefing so people know what to expect and what to question. |
| ChallengeProcedure | A stated procedure for challenging a person who is not recognised. |
| CloudSecurityBaseline | Covers cloud computing and hosting cybersecurity. |
| ConsentMechanism | A mechanism through which the person gives or withholds consent for the processing described. |
| ContinuityPlan | Covers resilience aspects of business continuity management. Exposure-reducing with respect to the missing manual override condition: a documented procedure does not restore operator control in real time, and therefore attaches through monitoredBy. |
| DefinedRoleAssignment | Covers cybersecurity roles and responsibilities. |
| DeviceControlPolicy | An enforced policy over what may be connected to a device. |
| DomainVerification | Technically enforced checking of the domain a person is sent to, such as a managed allowlist or managed resolution. |
| EntrySurveillance | Cameras or logging covering a physical entry point. |
| EquivalentAccessibleAlternative | A different route to the same outcome for a person who cannot complete the standard one. |
| ForcedSecureSetup | A mechanism preventing operation until the shipped configuration has been changed. |
| GuidedConfiguration | Step by step help for the person setting a device up. |
| GuidedPrivacySetup | Step by step help for the person choosing privacy settings. |
| HardenedOverridePath | A protected route by which an operator can take control. |
| HumanReview | A person reviews the decision before it takes effect. |
| InsiderActivityMonitoring | Watching what people who hold access actually do. |
| InterfaceAlert | A warning shown to the person at the moment of risk. |
| LeastPrivilegeEnforcement | Enforcement that a grant gives no more than the holder's work needs. |
| ManualOverride | A means by which a person can stop or take control of an automated service. |
| MediaScanning | Checking removable media before its contents are used. |
| MinimizationPrompt | A prompt asking the person to share only what is needed. |
| MultiFactorAuthentication | A second factor required beyond the password. |
| PartyIdentityVerification | Checking that the other party is who they claim to be. |
| PasswordStrengthEnforcement | An enforced rule on how strong a password must be. |
| PeriodicAccessReview | A repeating check of whether the people holding access still need it. |
| PeriodicSecurityAudit | Covers periodical cybersecurity review and audit. Broader in scope than periodic access review. |
| PermissionMinimisation | A supplier requirement or vetting process limiting the device permissions an application may request. Exposure-reducing rather than blocking: a process does not guarantee the resulting permission scope, so it attaches through monitoredBy. |
| PersonnelScreening | Covers cybersecurity in human resources. Likelihood-reducing with respect to the unconstrained insider access condition: screening does not remove privileged access, and therefore attaches through monitoredBy. |
| RetentionLimitEnforcement | Enforcement that data is not kept beyond its stated period. |
| RiskAssessmentProcess | Covers cybersecurity risk management. |
| SecureDevelopmentPractice | Covers cybersecurity in information and technology project management. |
| SecureDisposalProcedure | A stated procedure for destroying data or media at end of life. |
| SupervisedDelegation | A named person may complete the step for the user, under supervision. |
| ThirdPartySecurityAssessment | Covers third-party cybersecurity. |
| UnderstandablePurposeNotice | A notice telling the person, in terms they can follow, why the data is being taken and what will happen to it. |
| VerificationAid | Something that helps the person judge whether an approach is genuine. |
| WorkaroundDetection | Detection of people bypassing a control in order to get their work done. |

## LINDDUNCategory (7)

| Value | What it means |
| --- | --- |
| DataDisclosure | LINDDUN. Personal data becomes known to a party the person did not expect. |
| Detecting | Present for completeness of the LINDDUN framework. No condition class in this version asserts this category. |
| Identifying | Present for completeness of the LINDDUN framework. No condition class in this version asserts this category. |
| Linking | Present for completeness of the LINDDUN framework. No condition class in this version asserts this category. |
| NonCompliance | Present for completeness of the LINDDUN framework. No condition class in this version asserts this category. |
| NonRepudiation | Present for completeness of the LINDDUN framework. No condition class in this version asserts this category. |
| Unawareness | The LINDDUN U category, covering unawareness and unintervenability. |

## STRIDECategory (6)

| Value | What it means |
| --- | --- |
| DenialOfService | STRIDE. A service is made unavailable to the people who need it. |
| ElevationOfPrivilege | STRIDE. A party gains rights beyond those it was given. |
| InformationDisclosure | STRIDE. Information reaches a party that should not have it. |
| Repudiation | Present for completeness of the STRIDE framework. No condition class in this version asserts this category. |
| Spoofing | STRIDE. A party is taken for someone or something it is not. |
| Tampering | STRIDE. Data or a component is changed without authority. |

## DataCategory (5)

| Value | What it means |
| --- | --- |
| BehavioralData | Data about what a person does, such as movement, activity or patterns of use. |
| BiometricData | A special category of personal data under Article 9 of Regulation (EU) 2016/679 where processed for the purpose of unique identification. |
| FinancialData | Not an Article 9 special category. Classified as high sensitivity on the basis of direct financial harm following disclosure. |
| HealthData | A special category of personal data under Article 9 of Regulation (EU) 2016/679. |
| LocationData | Not an Article 9 special category. Classified as high sensitivity on the basis of re-identification and physical tracking risk in urban environments. |
| CredentialData | Passwords |

## LifecyclePhase (5)

| Value | What it means |
| --- | --- |
| Phase_ContinuousImprovement | Lifecycle phase. Reviewing the system and changing it over time. |
| Phase_Design | Lifecycle phase. Deciding how the system will work. |
| Phase_Implementation | Lifecycle phase. Building, configuring and installing the system. |
| Phase_Operation | Lifecycle phase. Running the system day to day. |
| Phase_Planning | Lifecycle phase. Deciding what the city will build and why. |

## VulnerabilityFactor (5)

| Value | What it means |
| --- | --- |
| Factor_Age_Child | The person is a child. |
| Factor_Age_Senior | The person is an older adult. |
| Factor_Disability | The person has a disability that affects how they use the system. |
| Factor_LowDigitalSkill | The person has limited digital skill or experience. |
| Factor_LowTrustScore | The person carries a low trust or reputation score assigned by a city system, which changes how the system treats them. |

## Authority (4)

| Value | What it means |
| --- | --- |
| Authority_AccessGranting | Can give or extend access to another person. |
| Authority_DataRelease | Can send personal data outside the organisation. |
| Authority_OperationalControl | Can change a physical setting or a live parameter. |
| Authority_PaymentRelease | Can cause money to move. |

## ConsequenceType (4)

| Value | What it means |
| --- | --- |
| Consequence_Financial | Harm to a person's money or financial standing. |
| Consequence_Operational | Interruption or degradation of a city service, without direct physical or privacy harm. |
| Consequence_Privacy | Harm to a person's privacy. |
| Consequence_Safety | Physical harm to persons. |

## ControlDomain (4)

| Value | What it means |
| --- | --- |
| Domain_Defense | Governance domain covering the technical protection of city systems. |
| Domain_Governance | Governance domain covering policy, roles and oversight. |
| Domain_Resilience | Governance domain covering continuity and recovery. |
| Domain_ThirdPartyCloud | Governance domain covering suppliers and hosted services. |

## TechnicalFunction (4)

| Value | What it means |
| --- | --- |
| Function_DataConsumption | Technical function. The element uses data to deliver something. |
| Function_DataGenerationAcquisition | Technical function. The element produces or collects data. |
| Function_DataInterfacingTransit | Technical function. The element carries data between other elements. |
| Function_DataStorageComputing | Technical function. The element holds or processes data. |

## DataSensitivityLevel (3)

| Value | What it means |
| --- | --- |
| Sensitivity_High | Disclosure would cause serious harm to the person. |
| Sensitivity_Low | Disclosure would cause little or no harm to the person. |
| Sensitivity_Medium | Disclosure would cause some harm to the person. |

## PrivacyConfigType (3)

| Value | What it means |
| --- | --- |
| Privacy_ByDefault | The service starts from the most protective privacy setting. |
| Privacy_Custom | The person has chosen the privacy setting themselves. |
| Privacy_Default | The service is still running the permissive setting it shipped with. |

## TrustBoundary (3)

| Value | What it means |
| --- | --- |
| CitizenToService | The line between a person and a city service they reach. |
| ServiceToCloud | The line between a city service and infrastructure a supplier runs. |
| ServiceToThirdParty | The line between a city service and a party outside the city. |

## AccessScope (2)

| Value | What it means |
| --- | --- |
| Scope_Privileged | The grant gives rights beyond ordinary use, such as administration. |
| Scope_Standard | The grant gives ordinary use rights only. |

## AutomationLevel (2)

| Value | What it means |
| --- | --- |
| Automation_High | Most of the service's work runs without a person acting. |
| Automation_Low | A person acts at each significant step. |

## DecisionImpact (2)

| Value | What it means |
| --- | --- |
| Impact_Consequential | The decision changes a person's rights, money, access or record. |
| Impact_Routine | The decision has no lasting effect on the person. |

## DecisionMechanism (2)

| Value | What it means |
| --- | --- |
| LearnedModel | The mechanism decides using a model trained on data. |
| RuleBasedLogic | The mechanism decides using rules written by a person. |

## DeviceConfigState (2)

| Value | What it means |
| --- | --- |
| Config_Customized | The device configuration was changed after installation. |
| Config_ShippedDefault | The device still runs the configuration it shipped with. |

## PermissionScope (2)

| Value | What it means |
| --- | --- |
| PermissionScope_BeyondPurpose | The application holds permissions wider than its stated purpose needs. |
| PermissionScope_Minimal | The application holds only the permissions its stated purpose needs. |

# 4. Datatype properties

Read each as: a Domain thing carries this property, and its value is a plain value of the given type rather than a link to another named thing. These are the only facts a description may state as a yes or no or a number

| Property | Domain → Range | What it means |
| --- | --- | --- |
| allowsRemovableMedia | UserEndpoint → xsd:Boolean ( ture or False) | An open port accepting removable media. Not a behavioural indicator. The unrestricted removable media condition requires the value true. Recording false is a coder judgement that can be quoted. |
| dataRetentionPeriod | DataStore xsd:integer, 0 or more | The number of days the store keeps its data before deletion. Always days, so convert years. |
