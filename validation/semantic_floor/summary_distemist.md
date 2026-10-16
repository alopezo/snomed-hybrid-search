# Semantic-floor ablation — DISTEMIST

Search-only linking, pre-process OFF, rerank OFF, Clinical finding filter, k = 10. Only
SEM_MIN_SIM / the truncation gate differ. See README.md for the rationale.

| variant | acc@1 | recall@5 | recall@10 | hier@1 | hier@10 | MRR |
|---|--:|--:|--:|--:|--:|--:|
| floor off (SEM_MIN_SIM=0) | 0.588 | 0.763 | 0.798 | 0.733 | 0.894 | 0.663 |
| floor 0.80 on every query (rejected) | 0.577 | 0.729 | 0.750 | 0.712 | 0.829 | 0.642 |
| **floor 0.80 only on truncated queries (served)** | 0.588 | 0.764 | 0.798 | 0.733 | 0.894 | 0.663 |

## floor 0.80 on every query (rejected) vs floor off

54 mentions changed rank · lost@10 47 · won@10 0

| mention | gold | rank off → on |
|---|---|---|
| abstinencia sexual | Currently not sexually active (finding) | 6 → — |
| adenopatías en ambas cadenas laterocervicales | Lateral cervical lymphadenopathy (disorder) | 2 → — |
| adenopatías fijas y duras en ambas regiones inguinales | Inguinal lymphadenopathy (disorder) | 3 → — |
| adenopatías regionales | Lymphadenopathy (disorder) | 2 → — |
| adherencias peritumorales paraaórticas y al mesocolon | Colonic adhesions (disorder) | 3 → — |
| ampolla grande en sector nasal de la conjuntiva bulbar | Lesion of conjunctiva (disorder) | 8 → — |
| anemización | Anemia (disorder) | 3 → — |
| apendagitis | Epiploic appendagitis (disorder) | 3 → — |
| Arterio y arteriolonefroesclerosis | Nephrosclerosis (disorder) | 9 → 8 |
| asimetría facial por aumento de volumen en la mejilla izquie | Facial asymmetry (disorder) | 1 → — |
| broncoaspiración durante la inducción anestésica | Perioperative pulmonary aspiration (disorder) | 3 → — |
| cicatriz | Scar (disorder) | 1 → 2 |
| cicatrización | Scar (disorder) | 4 → — |
| CID | Disseminated intravascular coagulation (disorder) | 6 → 4 |
| colección retroperitoneal | Retroperitoneal abscess (disorder) | 9 → — |
| colitis actínica | Colitis caused by ionizing radiation (disorder) | 1 → — |
| cuadro catarral | Common cold (disorder) | 8 → 6 |
| cuadro de carácter alérgico | Allergic disorder (disorder) | 4 → — |
| cóndilo bífido izquierdo anteroposterior | Bifid mandibular condyle (disorder) | 1 → — |
| cóndilo: bifidez anteroposterior | Bifid mandibular condyle (disorder) | 1 → — |
| diarrea (5-6 deposiciones diarias) con sangre roja | Hemorrhagic diarrhea (disorder) | 1 → — |
| dilatación del conducto pancreático principal | Pancreatic duct disorder (disorder) | 1 → — |
| duplicación condilar | Bifid mandibular condyle (disorder) | 1 → — |
| edema óseo en el escafoides | Edema of wrist (finding) | 4 → — |
| enfermedad coronaria aguda grave | Acute heart disease (disorder) | 4 → — |
| Enfermedad de Castleman, variante hialino-vascular | Angiofollicular lymph node hyperplasia, hyaline-vascular type (disorder) | 5 → — |
| enfermedad de VHL | Von Hippel-Lindau syndrome (disorder) | 3 → — |
| Enfermedad por Arañazo de Gato | Cat scratch disease (disorder) | 10 → — |
| enteritis actínica | Inflammation of intestine caused by ionizing radiation (disorder) | 2 → — |
| epifora | Excessive tear production (finding) | 8 → — |
| erupción maculosa que afecta ahora ya, a extremidades inferi | Generalized rash (disorder) | 7 → — |
| escarlatina | Scarlet fever (disorder) | 3 → 1 |
| ex-adicto a drogas por vía parenteral | Intravenous drug user (finding) | 2 → — |
| fractura-avulsión de la EIAI | Closed avulsion fracture of anterior inferior iliac spine of pelvis (disorder) | 2 → — |
| gingivitis localizada a nivel del 83,84 | Localized gingivitis (disorder) | 1 → — |
| gripe | Influenza (disorder) | 2 → — |
| hemangiomas fotocoagulados nasales | Hemangioma of nose (disorder) | 1 → — |
| hipo-proteinemia leve | Hypoproteinemia (disorder) | 1 → — |
| infectado por el VHC genotipo 1a | Viral hepatitis type C (disorder) | 6 → — |
| infiltraba la cabeza del páncreas | Metastatic malignant neoplasm to head of pancreas (disorder) | 4 → — |
| inflamación parotídea bilateral | Parotitis - non-mumps (disorder) | 3 → — |
| invasión de vía biliar | Metastatic malignant neoplasm to biliary tract (disorder) | 2 → — |
| ipertensión cerebral | Hypertensive encephalopathy (disorder) | 4 → — |
| LAM | Acute myeloid leukemia (disorder) | 2 → 3 |
| lesiones hipodensas hepáticas | Disorder of liver (disorder) | 8 → 5 |
| lesión nodular, inflamada, ambas en cuero cabelludo | Lesion of scalp (disorder) | 10 → — |
| lesión se extiende por el tejido adiposo subseroso del apénd | Mass of appendix (finding) | 2 → — |
| mamilas invertidas | Inversion of nipple (disorder) | 5 → — |
| MAV | Congenital arteriovenous malformation (disorder) | 2 → — |
| metástasis en 1/ 19 ganglios linfáticos | Metastatic malignant neoplasm to lymph node (disorder) | 7 → — |
| microabscesos | Abscess (disorder) | 1 → — |
| MM IgA-kappa | Immunoglobulin A myeloma (disorder) | 3 → — |
| púrpura de Schönlein-Henoch con afectación articular | Immunoglobulin A vasculitis (disorder) | 6 → — |
| quiste sinovial por encima de la rama frontal del nervio fac | Synovial cyst (disorder) | 9 → — |

## floor 0.80 only on truncated queries (served) vs floor off

2 mentions changed rank · lost@10 0 · won@10 0

| mention | gold | rank off → on |
|---|---|---|
| CID | Disseminated intravascular coagulation (disorder) | 6 → 4 |
| LAM | Acute myeloid leukemia (disorder) | 2 → 3 |

_n = 985 mentions · gemma off._
