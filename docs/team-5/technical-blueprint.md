### Informe de Autoevaluación de Portafolio de Título: Proyecto KalliCode (Hito M1)

#### 1\. Resumen y Abstracts (Español e Inglés) 

La capacidad de sintetizar arquitecturas de software de alta complejidad técnica es una competencia estratégica fundamental en el panorama tecnológico actual. Para un profesional de nivel Senior, no basta con la implementación técnica aislada; es imperativo comunicar la visión y los mecanismos de control de manera que sean validados tanto por audiencias académicas como por niveles directivos corporativos. Una comunicación técnica efectiva actúa como el puente entre la innovación disruptiva y la confianza organizacional, permitiendo que sistemas autónomos sean aceptados en entornos de misión crítica.**Resumen Ejecutivo**  El proyecto KalliCode se define bajo el paradigma de una  **"Célula de Entrega Agéntica"** , una factoría de software industrial impulsada por Inteligencia Artificial diseñada para operar en sectores altamente regulados. El enfoque del Equipo 5, denominado "Plataforma y Confianza", consiste en el diseño y despliegue de una infraestructura técnica robusta que prioriza la soberanía de los datos y la viabilidad operativa. La arquitectura se sustenta en un modelo SaaS multi-tenant que utiliza  **Row-Level Security (RLS)**  en PostgreSQL 16 para garantizar un aislamiento estricto entre organizaciones, complementado con una capa de auditoría inmutable mediante firmas atómicas y hashes encadenados.La transformación de un desarrollo tradicionalmente "artesanal" hacia un modelo "industrializado" se logra mediante la orquestación con Docker y Makefiles profesionales, junto con pipelines de CI/CD automatizados. Este enfoque mitiga los riesgos operativos inherentes a la IA, asegurando que la velocidad de los agentes autónomos esté siempre regulada por los  **"Three Locks of Corporate Quality"**  y los marcos de gobernanza humana, garantizando un proceso predecible y auditable.**Abstract**  Kallicode is an AI-driven software factory architected under the  **"Agentic Delivery Cell"**  paradigm. Team 5, leading the "Platform and Trust" pillar, has engineered a technical foundation centered on data sovereignty and operational reliability. The system features a multi-tenant SaaS architecture utilizing strict  **Row-Level Security (RLS)**  in PostgreSQL 16 and an immutable audit trail secured by atomic signatures and chained hashes. By orchestrating local environments with Docker and Makefiles, alongside automated CI/CD quality gates, the team transitions from handcrafted software development to a predictable, industrial process. This ensures that agentic velocity remains governed by human-centric quality locks and regulatory compliance.Este informe detalla la evolución y fundamentación técnica de este ecosistema, nacido de una colaboración estratégica con la empresa Alloxentric para robustecer la transición de conceptos de laboratorio a plataformas industriales.

#### 2\. Descripción del Proyecto Kallicode

El proyecto KalliCode surge de una colaboración directa con la empresa  **Alloxentric** , propietaria de la visión del producto. En este marco, el equipo de ingeniería actúa como el brazo ejecutor especializado en robustecer la arquitectura original, asegurando que la puesta en marcha funcional cumpla con los estándares de seguridad y escalabilidad de nivel Enterprise-Ready.

##### Naturaleza del Sistema

KalliCode es un ecosistema SaaS multi-tenant que automatiza el ciclo de vida de desarrollo de software (SDLC) mediante una tubería de seis etapas:  **Triage → Design → Build → QA → Security → Deploy** . La plataforma utiliza agentes especializados que interactúan con un sistema de  **"CodeMapping" híbrido** , el cual combina grafos de conocimiento y capas vectoriales para proporcionar un contexto profundo y libre de alucinaciones a los agentes que operan en el pipeline.

##### Componentes Clave

* **API REST (FastAPI):**  Punto de entrada principal que gestiona portales, webhooks y la comunicación interna del sistema.  
* **Workers Asíncronos (Redis Streams):**  Orquestan tareas de alta carga, como la normalización de tickets y la reconciliación de colas, garantizando la resiliencia del pipeline.  
* **Cerebro de CodeMapping:**  Motor de contexto que utiliza  **Neo4j**  para la ontología de negocio y  **Qdrant/pgvector**  para la indexación semántica.  
* **Identificadores ULID:**  Uso de identificadores universales con prefijos semánticos explícitos (ej. u\_ para usuarios, org\_ para organizaciones, job\_ para tareas) para garantizar trazabilidad total y ordenamiento cronológico en entornos distribuidos.La arquitectura exige una gestión sofisticada de modelos de lenguaje (LLM) mediante tres niveles:  **Flash, Pro y Fable** . Esta estratificación es una decisión arquitectónica crítica: permite derivar tareas de baja latencia y clasificación a modelos  **Flash** , reservar el razonamiento lógico intermedio para  **Pro** , y escalar a  **Fable**  solo en etapas de diseño complejo o seguridad donde el "context window" y la profundidad cognitiva son innegociables. Esta jerarquía optimiza drásticamente los costos operativos sin comprometer la integridad del razonamiento del sistema.

##### Categorización Tecnológica

Categoría,Tecnología,Versión / Detalle  
Lenguaje,Python,\>= 3.12  
Framework API,FastAPI,\>= 0.115 (Uvicorn \>= 0.30)  
Bases de Datos,PostgreSQL 16,RLS \+ pgvector para embeddings  
Grafo de Contexto,Neo4j,Ontología de negocio (200k+ nodos)  
Capa Vectorial,Qdrant / pgvector,BGE-M3 (1024 dims)  
Broker / Caché,Redis,7.0  (Streams \+ Consumer Groups)  
Tiers de LLM,Flash / Pro / Fable,Razonamiento y costo escalable  
Infraestructura,Docker / Makefile,Orquestación local y automatización  
La solidez de esta infraestructura técnica es el sustrato necesario para validar las competencias de ingeniería y seguridad en un entorno de alta exigencia profesional.

#### 3\. Relación con Competencias del Perfil de Egreso e Intereses Profesionales

La ejecución de un proyecto de esta magnitud requiere una alineación precisa entre la práctica técnica y los estándares académicos de titulación, garantizando que el desarrollo sea tanto innovador en términos de IA como riguroso en su base de ingeniería.

##### Validación de Competencias

KalliCode sirve como evidencia de cumplimiento en pilares fundamentales:

1. **Ingeniería de Software:**  Mediante el diseño de una máquina de estados para la gestión de jobs y la implementación de una arquitectura SaaS multi-tenant escalable.  
2. **Seguridad Informática:**  La implementación de firmas atómicas y hashes encadenados satisface los requisitos de transparencia del  **EU AI Act** , asegurando una auditoría inmutable de cada decisión tomada por la IA.  
3. **Gestión de Datos:**  El aislamiento absoluto mediante RLS y la gestión de secretos validan competencias avanzadas en soberanía de datos y privacidad.

##### Enfoque Individual (DevOps/SRE)

El rol de Alejandro González se centra en la creación de  **"carreteras" (Highways) de CI/CD**  de alta fidelidad. El objetivo es la eliminación de la fricción operativa mediante la automatización radical, estableciendo un "Estadio" de desarrollo reproducible que permita que la innovación agéntica progrese sin bloqueos de infraestructura.La implementación de un aislamiento estricto y soberanía de datos  *on-premise*  habilita estratégicamente a la empresa para operar en sectores altamente regulados (Fintech, Healthtech), donde la privacidad no es solo una característica, sino un requisito legal ineludible. Esta alineación garantiza la viabilidad técnica y estratégica del proyecto.

#### 4\. Argumentación de Factibilidad y Objetivos del Proyecto

El éxito de una plataforma de IA de nivel industrial no depende de la experimentación azarosa, sino de una planificación basada en hechos y en el uso de herramientas que garanticen un arranque determinista.

##### Factibilidad Técnica

Se aplica la metodología de  **"Desbloqueo Estricto"** , donde la infraestructura precede y habilita el desarrollo de aplicaciones. El uso de contenedores Docker y Makefiles profesionales permite levantar el stack completo en menos de 15 minutos. Tener una PoC local reproducible es la respuesta estratégica al desafío de recibir un proyecto corporativo, permitiendo iteraciones rápidas y seguras sin inconsistencias entre entornos.

##### Objetivos (Hito M1)

1. **Objetivo General:**  Establecer la infraestructura de plataforma y los mecanismos de confianza, garantizando un entorno automatizado y aislamiento de datos inexpugnable.  
2. **Objetivos Específicos:**  
3. **Aislamiento:**  Implementar RLS en PostgreSQL 16 y middleware de sesiones confinadas (sesion\_tenant()).  
4. **Automatización:**  Configurar orquestación local con targets de Makefile (bootstrap, up, db).  
5. **Gobernanza:**  Desplegar pipelines en GitHub Actions con puertas de calidad estáticas y de integración.  
6. **Inmutabilidad:**  Diseñar un esquema de auditoría "sellado en piedra" mediante triggers y firmas SHA-256.Esta base metodológica asegura que el proyecto pase de ser un concepto a una plataforma operativa real y escalable.

#### 5\. Propuesta Metodológica y Plan de Trabajo Detallado (Hito M1)

En sistemas de alta complejidad, el orden de precedencia técnica (Start-up Order) es vital para la estabilidad. La plataforma debe construirse sobre cimientos sólidos antes de añadir cualquier lógica agéntica.

##### Metodología de Trabajo

La propuesta se divide en cuatro fases críticas:

1. **Fase 1: Orquestación (Alejandro González):**  Creación de infraestructura local (PostgreSQL, Redis 7,  **Azurite**  para emulación de storage) y automatización del ciclo de vida del stack.  
2. **Fase 2: Tenencia (Ana Panes):**  Lógica de aislamiento en la base de datos y middleware de sesiones.  
3. **Fase 3: Seguridad (Luis Ibañez):**  Inmutabilidad y lógica criptográfica de los "Gates".  
4. **Fase 4: Integración (Equipo):**  Validación cruzada mediante pipelines automatizados.

##### Plan de Trabajo (7 Días)

Día,Responsable,Tarea Clave  
1-2,Alejandro González,Bootstrap:  Crear docker-compose.yml (inc. Azurite) y targets make up/bootstrap.  
2-3,Alejandro González,CI Highway:  Configurar .github/workflows/ci.yml (Ruff/Mypy strict).  
3-4,Ana Panes,Session Confinement:  Implementar sesion\_tenant() y políticas SQL de RLS.  
4-5,Ana Panes,Leak Testing:  Desarrollar tests/test\_no\_leak.py para validar aislamiento.  
5-6,Luis Ibañez,Immutable Audit:  Crear triggers T3 para bloqueo de UPDATE/DELETE.  
6-7,Luis Ibañez,Chained Hashes:  Lógica de sellado criptográfico para firmas de los Gates.  
7,Todos,M1 Clearance:  Ejecución y validación de suite de pruebas completa en CI.  
Este cronograma garantiza que cada tarea sea un habilitador técnico para la siguiente, eliminando cuellos de botella en la infraestructura.

#### 6\. Propuesta de Evidencias de Logro y Matriz de Objetivos-Evidencias

La calidad de nivel corporativo debe certificarse mediante evidencias técnicas inmutables que demuestren que el sistema cumple con los requisitos de confianza.

##### Descripción de Evidencias

* **Técnicas:**  Repositorio con Makefile funcional y contenedores configurados para PostgreSQL 16 y Redis 7\.  
* **Seguridad:**  Logs de éxito de test\_no\_leak.py y validación de triggers SQL que impidan la alteración de auditorías.  
* **Calidad:**  Reportes de GitHub Actions exitosos (Static, Unitary, Integration).El uso de herramientas de análisis estático como  **Ruff**  y  **Mypy (strict)**  es crítico: dado que los agentes generan código a una velocidad muy superior a la humana, el análisis estático es la única barrera que previene la acumulación de "deuda técnica a la velocidad de la luz", asegurando que la velocidad agéntica no comprometa la integridad del sistema.

##### Matriz de Objetivos-Evidencias

Objetivo Específico,Evidencia Correspondiente  
Aislamiento Multi-tenant,Script test\_no\_leak.py y políticas RLS documentadas.  
Automatización Local,Makefile funcional con targets up y bootstrap.  
Gobernanza y CI/CD,"Jobs exitosos en GitHub (Static, Unit, Integration)."  
Auditoría Inmutable,Tabla audit\_events con triggers de sellado activos.

#### 7\. Project Reflection e Individual Conclusions (English Only)

Meta-cognition in software engineering is a strategic imperative when transitioning from "handcrafted development" to an  **Agentic Delivery Cell** . The primary challenge of Project Kallicode is not just generating code, but managing the governance of that generation. While traditional co-pilots focus on individual typing speed, this project focuses on organizational orchestration and trust.**Project Reflection**  The implementation of the  **Three Locks of Corporate Quality**  (Human Governance Gates) acts as a critical differentiator. By embedding cryptographic integrity and strictly following the EU AI Act principles, we ensure that AI-driven velocity never bypasses human responsibility. The strategic shift from "typing" to "orchestration" means our value as engineers lies in building the robust, immutable "Highways" where agents can operate safely. The success of the "Platform and Trust" foundation is what ultimately allows a corporation to trust an autonomous pipeline with its most sensitive digital assets. Building these highways is what permits  **Agentic Velocity**  to be safely realized in a corporate environment.**Individual Conclusions**  Automation is the absolute "unlocker." By delivering a 15-minute reproducible stack and a multi-stage CI/CD pipeline, we eliminated the environmental friction that typically plagues early-stage projects. The infrastructure is no longer a blocker but a reliable stage for agentic execution. Articulating and building these robust "Highways" for AI agents defines the new value proposition for engineers in the age of autonomous pipelines, shifting the focus from manual labor to high-level architectural oversight and systematic reliability.  
