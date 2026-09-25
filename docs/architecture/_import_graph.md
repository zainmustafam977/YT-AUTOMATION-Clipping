# Module Import Graph & Call Topology

> **As-Implemented Dependency & Import Topology**  
> Generated from AST static analysis of all Python modules across `shorts_engine/`, `cli.py`, and `run.py`.  
> Date: 2026-09-24

---

## 1. Subsystem Layer Architecture

The codebase organizes strictly into seven functional tiers based on real import relationships:

```mermaid
graph TD
    classDef l0 fill:#1f2937,stroke:#3b82f6,stroke-width:2px,color:#fff;
    classDef l1 fill:#1e293b,stroke:#06b6d4,stroke-width:2px,color:#fff;
    classDef l2 fill:#0f172a,stroke:#10b981,stroke-width:2px,color:#fff;
    classDef l3 fill:#18181b,stroke:#f59e0b,stroke-width:2px,color:#fff;
    classDef l4 fill:#27272a,stroke:#ec4899,stroke-width:2px,color:#fff;
    classDef l5 fill:#262626,stroke:#8b5cf6,stroke-width:2px,color:#fff;
    classDef l6 fill:#171717,stroke:#ef4444,stroke-width:2px,color:#fff;

    subgraph Layer_6 [Layer 6: User & System Interfaces]
        CLI[cli.py / run.py]:::l6
        DASH[shorts_engine.dashboard]:::l6
    end

    subgraph Layer_5 [Layer 5: Orchestration & Execution]
        ORCH[shorts_engine.orchestration]:::l5
        WORK[shorts_engine.services.worker]:::l5
    end

    subgraph Layer_4 [Layer 4: Rendering & Quality Gates]
        REND[shorts_engine.services.rendering]:::l4
        QGATE[shorts_engine.services.quality_gate]:::l4
    end

    subgraph Layer_3 [Layer 3: Composite Domain Services]
        INGEST[services.ingestion]:::l3
        CLIP[services.clip_selection]:::l3
        CAPT[services.captioning]:::l3
        PUB[services.publishing]:::l3
        AB[services.ab_testing]:::l3
        DET[services.detection]:::l3
    end

    subgraph Layer_2 [Layer 2: Core Domain Leaf Services]
        LLM[services.llm]:::l2
        TRANS[services.transcription]:::l2
        REFRAME[services.reframing]:::l2
        AUDIO[services.audio]:::l2
        BROLL[services.broll]:::l2
        QMED[services.quality - Media QA]:::l2
        MON[services.monitoring]:::l2
        ANALYTICS[services.analytics]:::l2
    end

    subgraph Layer_1 [Layer 1: Persistence & Storage]
        DB[shorts_engine.db]:::l1
        STOR[services.storage]:::l1
    end

    subgraph Layer_0 [Layer 0: System Foundation]
        CONF[shorts_engine.config]:::l0
        EXEC[services.execution - Watchdog]:::l0
        TELEM[services.telemetry]:::l0
    end

    %% Dependency Connections
    CLI --> DASH
    CLI --> ORCH
    CLI --> WORK
    CLI --> PUB
    CLI --> STOR

    DASH --> ORCH
    DASH --> DB
    DASH --> QGATE
    DASH --> REND
    DASH --> PUB

    ORCH --> WORK
    ORCH --> INGEST
    ORCH --> TRANS
    ORCH --> CLIP
    ORCH --> REFRAME
    ORCH --> CAPT
    ORCH --> REND
    ORCH --> QGATE
    ORCH --> QMED
    ORCH --> PUB

    WORK --> REFRAME
    WORK --> CAPT
    WORK --> REND
    WORK --> QGATE
    WORK --> QMED
    WORK --> DB

    REND --> AUDIO
    REND --> CAPT
    REND --> REFRAME
    REND --> STOR
    REND --> EXEC
    REND -.->|Lazy import in trimmer| QGATE

    QGATE --> TRANS
    QGATE --> CAPT
    QGATE --> REFRAME
    QGATE --> LLM
    QGATE -.->|Lazy import in recovery| REND

    INGEST --> EXEC
    INGEST --> STOR
    INGEST --> TELEM

    CLIP --> LLM
    CLIP --> TRANS

    CAPT --> TRANS

    AB --> LLM
    AB --> PUB
    AB --> DB

    DET --> DB

    TRANS --> TELEM

    AUDIO --> EXEC
    QMED --> EXEC
    STOR --> EXEC

    DB --> CONF
    STOR --> CONF
    EXEC --> CONF
    LLM --> CONF
```

---

## 2. Package Dependency Out-Degree Matrix

This table lists every internal package, its out-degree (number of internal packages it imports), and the exact list of targets:

| Package | Out-Degree | Internal Target Packages Imported |
| :--- | :--- | :--- |
| **`shorts_engine.config`** | 0 | *(None - Pure leaf foundation)* |
| **`shorts_engine.services.execution`** | 0 | *(None - Depends only on psutil and standard library)* |
| **`shorts_engine.services.telemetry`** | 0 | *(None - In-memory and SSE progress tracker)* |
| **`shorts_engine.db`** | 1 | `shorts_engine.config` |
| **`shorts_engine.services.analytics`** | 1 | `shorts_engine.db` |
| **`shorts_engine.services.broll`** | 1 | `shorts_engine.config` |
| **`shorts_engine.services.llm`** | 1 | `shorts_engine.config` |
| **`shorts_engine.services.reframing`** | 1 | `shorts_engine.config` |
| **`run.py`** | 2 | `cli`, `shorts_engine.config` |
| **`shorts_engine.services.audio`** | 2 | `shorts_engine.config`, `shorts_engine.services.execution` |
| **`shorts_engine.services.captioning`** | 2 | `shorts_engine.config`, `shorts_engine.services.transcription` |
| **`shorts_engine.services.detection`** | 2 | `shorts_engine.config`, `shorts_engine.db` |
| **`shorts_engine.services.monitoring`** | 2 | `shorts_engine.config`, `shorts_engine.db` |
| **`shorts_engine.services.publishing`** | 2 | `shorts_engine.config`, `shorts_engine.db` |
| **`shorts_engine.services.quality`** | 2 | `shorts_engine.config`, `shorts_engine.services.execution` |
| **`shorts_engine.services.storage`** | 2 | `shorts_engine.config`, `shorts_engine.services.execution` |
| **`shorts_engine.services.transcription`** | 2 | `shorts_engine.config`, `shorts_engine.services.telemetry` |
| **`shorts_engine.services.clip_selection`** | 3 | `shorts_engine.config`, `shorts_engine.services.llm`, `shorts_engine.services.transcription` |
| **`shorts_engine.services.ab_testing`** | 4 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.services.llm`, `shorts_engine.services.publishing` |
| **`shorts_engine.services.ingestion`** | 5 | `shorts_engine.config`, `shorts_engine.services.execution`, `shorts_engine.services.rendering`, `shorts_engine.services.storage`, `shorts_engine.services.telemetry` |
| **`shorts_engine.services.quality_gate`** | 7 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.services.captioning`, `shorts_engine.services.llm`, `shorts_engine.services.reframing`, `shorts_engine.services.rendering` *(lazy)*, `shorts_engine.services.transcription` |
| **`shorts_engine.services.rendering`** | 9 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.services.audio`, `shorts_engine.services.captioning`, `shorts_engine.services.execution`, `shorts_engine.services.quality_gate` *(lazy)*, `shorts_engine.services.reframing`, `shorts_engine.services.storage`, `shorts_engine.services.transcription` |
| **`cli.py`** | 10 | `shorts_engine.config`, `shorts_engine.dashboard`, `shorts_engine.db`, `shorts_engine.orchestration`, `shorts_engine.services.detection`, `shorts_engine.services.ingestion`, `shorts_engine.services.monitoring`, `shorts_engine.services.publishing`, `shorts_engine.services.storage`, `shorts_engine.services.worker` |
| **`shorts_engine.services.worker`** | 10 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.services.captioning`, `shorts_engine.services.monitoring`, `shorts_engine.services.quality`, `shorts_engine.services.quality_gate`, `shorts_engine.services.reframing`, `shorts_engine.services.rendering`, `shorts_engine.services.storage`, `shorts_engine.services.transcription` |
| **`shorts_engine.orchestration`** | 17 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.services.broll`, `shorts_engine.services.captioning`, `shorts_engine.services.clip_selection`, `shorts_engine.services.detection`, `shorts_engine.services.ingestion`, `shorts_engine.services.monitoring`, `shorts_engine.services.publishing`, `shorts_engine.services.quality`, `shorts_engine.services.quality_gate`, `shorts_engine.services.reframing`, `shorts_engine.services.rendering`, `shorts_engine.services.storage`, `shorts_engine.services.telemetry`, `shorts_engine.services.transcription`, `shorts_engine.services.worker` |
| **`shorts_engine.dashboard`** | 19 | `shorts_engine.config`, `shorts_engine.db`, `shorts_engine.orchestration`, `shorts_engine.services.ab_testing`, `shorts_engine.services.analytics`, `shorts_engine.services.captioning`, `shorts_engine.services.clip_selection`, `shorts_engine.services.detection`, `shorts_engine.services.ingestion`, `shorts_engine.services.llm`, `shorts_engine.services.monitoring`, `shorts_engine.services.publishing`, `shorts_engine.services.quality_gate`, `shorts_engine.services.reframing`, `shorts_engine.services.rendering`, `shorts_engine.services.storage`, `shorts_engine.services.telemetry`, `shorts_engine.services.transcription`, `shorts_engine.services.worker` |

---

## 3. Dependency Cycles & Tightly Coupled Areas

### Cycle Found: `rendering` <--> `quality_gate`
- **Location 1:** [`shorts_engine/services/rendering/trimmer.py:24-25`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/rendering/trimmer.py#L24-L25) imports:
  - `shorts_engine.services.quality_gate.technical_check.TechnicalVerifier`
  - `shorts_engine.services.quality_gate.render_inspector.RenderInspector`
- **Location 2:** [`shorts_engine/services/quality_gate/recovery.py:306`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/quality_gate/recovery.py#L306) imports:
  - `shorts_engine.services.rendering.compositor.VideoCompositor` (inline inside `auto_fix_clip()`).
- **Architectural Impact:**
  - This cycle is actively mitigated in code by using an **inline/deferred import** at line 306 in `recovery.py`. However, it demonstrates coupling between trimming/re-rendering logic and quality-check logic.
  - **Refactoring Recommendation:** Move shared inspection contracts into an independent interface module (`shorts_engine.services.quality_contracts`).
