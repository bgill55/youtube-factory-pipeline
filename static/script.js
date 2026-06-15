document.addEventListener("DOMContentLoaded", () => {
    // API endpoints
    const API_CONFIG = "/api/config";
    const API_RUN = "/api/run";
    const API_RUNS = "/api/runs";
    const API_LOGS = "/api/logs/";

    // State variables
    let currentConfig = {};
    let activeRunId = null;
    let selectedRunId = null;
    let runsList = [];
    let logInterval = null;
    let runsInterval = null;

    // DOM Elements
    const configForm = document.getElementById("config-form");
    const runForm = document.getElementById("run-pipeline-form");
    const schedulerForm = document.getElementById("scheduler-form");
    const logOutput = document.getElementById("log-output");
    const logsSelector = document.getElementById("logs-selector");
    const refreshLogsBtn = document.getElementById("refresh-logs-btn");
    const runsListContainer = document.getElementById("runs-list-container");
    const videoPlaceholder = document.getElementById("video-player-placeholder");
    const videoWrapper = document.getElementById("video-player-wrapper");
    const videoPlayer = document.getElementById("video-player");
    const nowPlayingTitle = document.getElementById("now-playing-title");
    const downloadVideoLink = document.getElementById("download-video-link");
    const toast = document.getElementById("toast");

    const activeRunIndicator = document.getElementById("active-run-indicator");
    const activeRunIdDisplay = document.getElementById("active-run-id");
    const nextRunDisplay = document.getElementById("next-run-display");
    const nextRunTime = document.getElementById("next-run-time");

    // Assets DOM
    const assetsWrapper = document.getElementById("run-assets-wrapper");
    const assetsUploadZone = document.getElementById("assets-upload-zone");
    const assetsFileInput = document.getElementById("assets-file-input");
    const assetsProgress = document.getElementById("assets-upload-progress");
    const assetsProgressBar = document.getElementById("assets-progress-bar");
    const assetsProgressText = document.getElementById("assets-progress-text");
    const assetsList = document.getElementById("assets-list");

    // Initialize Page
    init();

    function init() {
        loadConfig();
        loadRuns();
        initAssetsUpload();
        initAssetFirstMode();
        
        // Start polling runs list every 5 seconds to capture updates
        runsInterval = setInterval(loadRuns, 5000);

        // Event listeners
        const ovVoiceSelect = document.getElementById("omnivoice-voice-id");
        const ovCustomGroup = document.getElementById("omnivoice-custom-voice-group");
        ovVoiceSelect.addEventListener("change", () => {
            if (ovVoiceSelect.value === "custom") {
                ovCustomGroup.classList.remove("hidden");
            } else {
                ovCustomGroup.classList.add("hidden");
            }
        });

        configForm.addEventListener("submit", handleSaveConfig);
        runForm.addEventListener("submit", handleTriggerRun);
        schedulerForm.addEventListener("submit", handleSaveScheduler);
        refreshLogsBtn.addEventListener("click", () => {
            if (selectedRunId) loadLogs(selectedRunId);
        });
        logsSelector.addEventListener("change", handleLogSelection);

        // Clear fields button
        const clearBtn = document.getElementById("btn-clear-generator-fields");
        if (clearBtn) {
            clearBtn.addEventListener("click", () => {
                document.getElementById("run-seed").value = "";
                document.getElementById("run-audience").value = "";
                document.getElementById("run-competitors").value = "";
            });
        }

        // AI Suggest Metadata button
        const suggestMetadataBtn = document.getElementById("btn-suggest-metadata");
        if (suggestMetadataBtn) {
            suggestMetadataBtn.addEventListener("click", async () => {
                const seedVal = document.getElementById("run-seed").value.trim();
                if (!seedVal) {
                    showToast("Please enter a subject matter or concept seed first.", "error");
                    return;
                }

                suggestMetadataBtn.disabled = true;
                const origHtml = suggestMetadataBtn.innerHTML;
                suggestMetadataBtn.innerHTML = `<span class="material-symbols-outlined animate-spin" style="font-size: 14px;">sync</span> <span>Analyzing...</span>`;

                try {
                    const res = await fetch("/api/topics/suggest-metadata", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ seed: seedVal })
                    });
                    const data = await res.json();
                    
                    if (!res.ok) {
                        throw new Error(data.error || "Failed to suggest metadata");
                    }

                    if (data.target_audience) {
                        document.getElementById("run-audience").value = data.target_audience;
                    }
                    if (data.competitor_analysis) {
                        document.getElementById("run-competitors").value = data.competitor_analysis;
                    }

                    showToast("Metadata auto-populated successfully!");
                } catch (err) {
                    showToast(err.message, "error");
                } finally {
                    suggestMetadataBtn.disabled = false;
                    suggestMetadataBtn.innerHTML = origHtml;
                }
            });
        }

        // Reuse settings button
        const loadMetaBtn = document.getElementById("btn-load-meta-inputs");
        if (loadMetaBtn) {
            loadMetaBtn.addEventListener("click", () => {
                if (!selectedRunId) return;
                const run = runsList.find(r => r.run_id === selectedRunId);
                if (run) {
                    const seedVal = run.topic_seed || "";
                    document.getElementById("run-seed").value = seedVal.toLowerCase() === "[scraped]" ? "" : seedVal;
                    document.getElementById("run-audience").value = run.target_audience || "";
                    document.getElementById("run-competitors").value = run.competitor_analysis || "";
                    
                    // Load Asset-First settings if this run was Asset-First
                    if (run.asset_first) {
                        document.getElementById("asset-first-mode").checked = true;
                        // Show upload zone
                        document.getElementById("asset-upload-zone").classList.remove("hidden");
                        // Store asset info for submission
                        window.__pendingAssetFile = {
                            name: run.asset_video,
                            path: run.asset_video_path,
                            is_reused: true
                        };
                        // Show uploaded info
                        document.getElementById("asset-upload-zone").classList.add("hidden");
                        document.getElementById("asset-upload-progress").classList.add("hidden");
                        document.getElementById("asset-uploaded-info").classList.remove("hidden");
                        document.getElementById("asset-uploaded-name").textContent = 
                            `${run.asset_video} (from run ${run.run_id})`;
                    } else {
                        // Ensure checkbox is unchecked if not asset-first
                        document.getElementById("asset-first-mode").checked = false;
                        document.getElementById("asset-upload-zone").classList.add("hidden");
                        document.getElementById("asset-uploaded-info").classList.add("hidden");
                        window.__pendingAssetFile = null;
                    }
                    
                    // Scroll smoothly to the generator card
                    document.querySelector(".card-generator").scrollIntoView({ behavior: "smooth" });
                    showToast("Settings loaded into generator form!");
                }
            });
        }

        // Generate Short button
        const generateShortBtn = document.getElementById("btn-generate-short");
        if (generateShortBtn) {
            generateShortBtn.addEventListener("click", async () => {
                if (!selectedRunId) return;
                
                generateShortBtn.disabled = true;
                const statusInfo = document.getElementById("short-status-info");
                const statusText = document.getElementById("short-status-text");
                
                if (statusInfo) statusInfo.classList.remove("hidden");
                if (statusText) statusText.textContent = "Requesting Short generation...";
                
                try {
                    const res = await fetch(`/api/runs/${selectedRunId}/generate_short`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || "Generation request failed");
                    
                    showToast("Short compilation triggered! Check progress below.");
                    
                    // Trigger a immediate reload of runs list to update state and set loader
                    loadRuns();
                } catch (err) {
                    showToast("Short generation failed: " + err.message, "error");
                    generateShortBtn.disabled = false;
                    if (statusInfo) statusInfo.classList.add("hidden");
                }
            });
        }

        // Upload Short to YouTube button
        const uploadShortBtn = document.getElementById("btn-upload-short");
        if (uploadShortBtn) {
            uploadShortBtn.addEventListener("click", async () => {
                if (!selectedRunId) return;

                uploadShortBtn.disabled = true;
                const wrapper  = document.getElementById("short-upload-status-wrapper");
                const loader   = document.getElementById("short-upload-loader");
                const doneDiv  = document.getElementById("short-upload-done");
                const failDiv  = document.getElementById("short-upload-failed");

                if (wrapper) wrapper.classList.remove("hidden");
                if (loader)  loader.classList.remove("hidden");
                if (doneDiv) doneDiv.classList.add("hidden");
                if (failDiv) failDiv.classList.add("hidden");

                try {
                    const res = await fetch(`/api/runs/${selectedRunId}/upload_short`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || "Upload request failed");

                    showToast("Short upload triggered! YouTube will process it shortly.");
                    // Polling in loadRuns() will handle the final state update
                    loadRuns();
                } catch (err) {
                    showToast("Short upload failed: " + err.message, "error");
                    uploadShortBtn.disabled = false;
                    if (loader)  loader.classList.add("hidden");
                    if (failDiv) {
                        failDiv.classList.remove("hidden");
                        const errText = document.getElementById("short-upload-error-text");
                        if (errText) errText.textContent = err.message;
                    }
                }
            });
        }


        // Resume run button
        const resumeBtn = document.getElementById("btn-resume-run");
        if (resumeBtn) {
            resumeBtn.addEventListener("click", async () => {
                if (!selectedRunId) return;
                
                resumeBtn.disabled = true;
                try {
                    const res = await fetch(`/api/runs/${selectedRunId}/resume`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || "Resume failed");
                    
                    showToast("Pipeline resume triggered successfully!");
                    activeRunId = selectedRunId;
                    
                    // Update running display
                    activeRunIndicator.classList.remove("hidden");
                    activeRunIdDisplay.textContent = activeRunId;
                    
                    // Switch to live log polling and update dropdown selection
                    logsSelector.value = activeRunId;
                    startLogPolling(activeRunId);
                    
                    // Refresh runs list to update the status in the sidebar
                    loadRuns();
                } catch (err) {
                    showToast("Failed to resume run: " + err.message, "error");
                } finally {
                    resumeBtn.disabled = false;
                }
            });
        }

        // Cancel run button
        const cancelBtn = document.getElementById("btn-cancel-run");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", async () => {
                if (!selectedRunId) return;
                if (!confirm("Kill this pipeline run? It will stop after the current stage completes.")) return;
                
                cancelBtn.disabled = true;
                try {
                    const res = await fetch(`/api/runs/${selectedRunId}/cancel`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || "Cancel failed");
                    
                    showToast("Pipeline cancelled. It will stop after the current stage.");
                    loadRuns();
                } catch (err) {
                    showToast("Failed to cancel run: " + err.message, "error");
                } finally {
                    cancelBtn.disabled = false;
                }
            });
        }
    function showToast(message, type = "success") {
        toast.textContent = message;
        toast.className = `toast ${type}`;
        toast.classList.remove("hidden");
        
        setTimeout(() => {
            toast.classList.add("hidden");
        }, 4000);
    }

    async function loadBackgroundMusicOptions(selectedVal) {
        try {
            const res = await fetch("/api/background-music");
            if (!res.ok) throw new Error("Failed to load background music list");
            const data = await res.json();
            
            const selectEl = document.getElementById("bg-music");
            selectEl.innerHTML = `
                <option value="none">None (No BGM)</option>
                <option value="stable_audio_3">Stable Audio 3 (AI Generated)</option>
            `;
            
            data.forEach(filename => {
                const opt = document.createElement("option");
                opt.value = filename;
                opt.textContent = filename;
                selectEl.appendChild(opt);
            });
            
            selectEl.value = selectedVal || "none";
        } catch (err) {
            console.error("Could not load BGM options", err);
        }
    }

    // 1. Config logic
    async function loadConfig() {
        try {
            const res = await fetch(API_CONFIG);
            if (!res.ok) throw new Error("Could not load config");
            
            const data = await res.json();
            currentConfig = data;
            
            // Helper to safely set element value
            const safeSetValue = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.value = value;
                else console.warn("[Config] Element not found: " + id);
            };
            const safeSetChecked = (id, checked) => {
                const el = document.getElementById(id);
                if (el) el.checked = checked;
                else console.warn("[Config] Element not found: " + id);
            };
            
            // Populate settings form with safe setters
            safeSetValue("text-provider", data.text_provider || "freellmapi");
            safeSetValue("voice-provider", data.voice_provider || "edge_tts");
            safeSetValue("image-provider", data.image_provider || "pollinations");
            safeSetValue("freellmapi-key", data.freellmapi?.api_key || "");
            safeSetValue("gemini-api-key", data.gemini?.api_key || "");
            safeSetValue("gemini-image-model", data.gemini?.image_model || "gemini-3.1-flash-image");
            safeSetValue("gemini-text-model", data.gemini?.text_model || "gemini-2.5-flash");
            safeSetValue("local-sd-url", data.local_sd?.base_url || "http://127.0.0.1:7860");
            safeSetValue("local-sd-lora-url", data.local_sd?.lora_url || "");
            safeSetValue("local-sd-lora-strength", data.local_sd?.lora_strength !== undefined ? data.local_sd.lora_strength : 0.8);
            safeSetValue("local-sd-lora-trigger", data.local_sd?.lora_trigger || "");
            safeSetValue("pexels-api-key", data.pexels_api_key || "");
            safeSetValue("fal-api-key", data.fal_api_key || "");
            safeSetValue("cerebras-api-key", data.cerebras?.api_key || "");
            safeSetValue("groq-api-key", data.groq?.api_key || "");
            safeSetValue("groq-text-model", data.groq?.model_name || "llama-3.3-70b-versatile");
            safeSetValue("zai-api-key", data.zai?.api_key || "");
            safeSetValue("zai-text-model", data.zai?.model_name || "glm-4-plus");
            
            safeSetValue("elevenlabs-api-key", data.elevenlabs?.api_key || "");
            safeSetValue("elevenlabs-voice-id", data.elevenlabs?.voice_id || "");
            safeSetValue("omnivoice-base-url", data.omnivoice?.base_url || "http://127.0.0.1:3900/v1");
            
            // Handle OmniVoice voice dropdown
            const ovVoiceId = data.omnivoice?.voice_id || "default";
            const ovVoiceSelectLoad = document.getElementById("omnivoice-voice-id");
            const ovCustomGroupLoad = document.getElementById("omnivoice-custom-voice-group");
            const ovCustomInputLoad = document.getElementById("omnivoice-custom-voice-id");
            if (ovVoiceSelectLoad) {
                const isPresetOption = Array.from(ovVoiceSelectLoad.options).some(opt => opt.value === ovVoiceId);
                if (isPresetOption) {
                    ovVoiceSelectLoad.value = ovVoiceId;
                    if (ovCustomGroupLoad) ovCustomGroupLoad.classList.add("hidden");
                    if (ovCustomInputLoad) ovCustomInputLoad.value = "";
                } else {
                    ovVoiceSelectLoad.value = "custom";
                    if (ovCustomGroupLoad) ovCustomGroupLoad.classList.remove("hidden");
                    if (ovCustomInputLoad) ovCustomInputLoad.value = ovVoiceId;
                }
            }
            
            safeSetValue("omnivoice-model-name", data.omnivoice?.model_name || "omnivoice");
            safeSetValue("voice-fallback", data.voice_fallback?.tts_voice || "en-US-GuyNeural");
            
            safeSetValue("lm-studio-url", data.lm_studio?.base_url || "http://localhost:1234/v1");
            safeSetValue("lm-studio-model", data.lm_studio?.model_name || "");
            
            safeSetValue("video-width", data.video_settings?.width || 1920);
            safeSetValue("video-height", data.video_settings?.height || 1080);
            safeSetValue("video-aspect", data.video_settings?.aspect_ratio || "16:9");
            safeSetValue("video-sub-delay", data.video_settings?.subtitle_delay !== undefined ? data.video_settings.subtitle_delay : 0.15);
            
            // Populate BGM and audio settings
            await loadBackgroundMusicOptions(data.bg_music);
            safeSetValue("bg-music-volume", data.audio_settings?.bg_music_volume !== undefined ? data.audio_settings.bg_music_volume : 0.15);
            safeSetValue("ducking-threshold", data.audio_settings?.ducking_threshold !== undefined ? data.audio_settings.ducking_threshold : 0.10);
            safeSetValue("ducking-ratio", data.audio_settings?.ducking_ratio !== undefined ? data.audio_settings.ducking_ratio : 4.0);
            safeSetValue("ducking-release", data.audio_settings?.ducking_release !== undefined ? data.audio_settings.ducking_release : 800);
            
            safeSetValue("upload-privacy", data.upload_settings?.privacy_status || "private");
            
            // Populate scheduler card
            const sched = data.scheduler || {};
            safeSetChecked("sched-enabled", sched.enabled || false);
            safeSetValue("sched-cron", data.sched?.cron || "0 12 * * *");
            safeSetValue("sched-seed", data.sched?.seed || "");
            safeSetValue("sched-audience", data.sched?.audience || "");
            
            // Display next run badge if active
            updateNextRunBadge(sched);
            
        } catch (err) {
            showToast("Failed to load configurations: " + err.message, "error");
        }
    }


    function updateNextRunBadge(sched) {
        if (sched.enabled && sched.next_run) {
            nextRunDisplay.classList.remove("hidden");
            const dt = new Date(sched.next_run);
            nextRunTime.textContent = dt.toLocaleString();
        } else {
            nextRunDisplay.classList.add("hidden");
        }
    }

    async function handleSaveConfig(e) {
        e.preventDefault();
        
        // Assemble updated config structure
        const updatedConfig = {
            ...currentConfig,
            text_provider: document.getElementById("text-provider").value,
            voice_provider: document.getElementById("voice-provider").value,
            image_provider: document.getElementById("image-provider").value,
            freellmapi: {
                ...(currentConfig.freellmapi || {}),
                api_key: document.getElementById("freellmapi-key").value
            },
            pexels_api_key: document.getElementById("pexels-api-key").value,
            fal_api_key: document.getElementById("fal-api-key").value,
            cerebras: {
                ...(currentConfig.cerebras || {}),
                api_key: document.getElementById("cerebras-api-key").value
            },
            bg_music: document.getElementById("bg-music").value,
            audio_settings: {
                bg_music_volume: parseFloat(document.getElementById("bg-music-volume").value || "0.15"),
                ducking_threshold: parseFloat(document.getElementById("ducking-threshold").value || "0.10"),
                ducking_ratio: parseFloat(document.getElementById("ducking-ratio").value || "4.0"),
                ducking_attack: 200,
                ducking_release: parseInt(document.getElementById("ducking-release").value || "800")
            },
            gemini: {
                api_key: document.getElementById("gemini-api-key").value,
                image_model: document.getElementById("gemini-image-model").value,
                text_model: document.getElementById("gemini-text-model").value
            },
            local_sd: {
                base_url: document.getElementById("local-sd-url").value,
                steps: currentConfig.local_sd?.steps || 25,
                cfg_scale: currentConfig.local_sd?.cfg_scale || 7.0,
                lora_url: document.getElementById("local-sd-lora-url").value,
                lora_strength: parseFloat(document.getElementById("local-sd-lora-strength").value || "0.8"),
                lora_trigger: document.getElementById("local-sd-lora-trigger").value
            },
            groq: {
                ...(currentConfig.groq || {}),
                api_key: document.getElementById("groq-api-key").value,
                model_name: document.getElementById("groq-text-model").value
            },
            zai: {
                ...(currentConfig.zai || {}),
                api_key: document.getElementById("zai-api-key").value,
                model_name: document.getElementById("zai-text-model").value
            },
            elevenlabs: {
                api_key: document.getElementById("elevenlabs-api-key").value,
                voice_id: document.getElementById("elevenlabs-voice-id").value
            },
            omnivoice: {
                base_url: document.getElementById("omnivoice-base-url").value,
                voice_id: document.getElementById("omnivoice-voice-id").value === "custom" ? 
                    document.getElementById("omnivoice-custom-voice-id").value : 
                    document.getElementById("omnivoice-voice-id").value,
                model_name: document.getElementById("omnivoice-model-name").value,
                language: currentConfig.omnivoice?.language || "en",
                speed: parseFloat(currentConfig.omnivoice?.speed || "1.0")
            },
            voice_fallback: {
                tts_voice: document.getElementById("voice-fallback").value
            },
            lm_studio: {
                base_url: document.getElementById("lm-studio-url").value,
                model_name: document.getElementById("lm-studio-model").value
            },
            video_settings: {
                width: parseInt(document.getElementById("video-width").value),
                height: parseInt(document.getElementById("video-height").value),
                aspect_ratio: document.getElementById("video-aspect").value,
                subtitle_delay: parseFloat(document.getElementById("video-sub-delay").value || "0.15")
            },
            upload_settings: {
                privacy_status: document.getElementById("upload-privacy").value
            }
        };

        try {
            const res = await fetch(API_CONFIG, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(updatedConfig)
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Save failed");

            currentConfig = updatedConfig;
            showToast("Configurations saved successfully!");
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    async function handleSaveScheduler(e) {
        e.preventDefault();

        const updatedConfig = {
            ...currentConfig,
            scheduler: {
                enabled: document.getElementById("sched-enabled").checked,
                cron: document.getElementById("sched-cron").value,
                seed: document.getElementById("sched-seed").value,
                audience: document.getElementById("sched-audience").value
            }
        };

        try {
            const res = await fetch(API_CONFIG, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(updatedConfig)
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Save scheduler failed");

            currentConfig = updatedConfig;
            
            // Reload configuration to get next scheduled run time
            await loadConfig();
            showToast("Scheduler settings updated successfully!");
        } catch (err) {
            showToast(err.message, "error");
        }
    }

    // 2. Trigger run logic
    async function handleTriggerRun(e) {
        e.preventDefault();

        const triggerBtn = document.getElementById("trigger-run-btn");
        const seed = document.getElementById("run-seed").value;
        const audience = document.getElementById("run-audience").value;
        const competitors = document.getElementById("run-competitors").value;
        const assetFirstMode = document.getElementById("asset-first-mode")?.checked;
        const assetFile = window.__pendingAssetFile || document.getElementById("asset-file-input")?.files?.[0];

        // Visual feedback
        triggerBtn.disabled = true;

        try {
            let runId;

            if (assetFirstMode && assetFile) {
                // Asset-First mode: Send file upload in the initial run creation request (multipart/form-data)
                // This guarantees the asset is uploaded and saved BEFORE the pipeline execution starts
                const progress = document.getElementById("asset-upload-progress");
                const progressText = document.getElementById("asset-progress-text");
                const progressBar = document.getElementById("asset-progress-bar");
                
                if (progress) progress.classList.remove("hidden");
                if (progressBar) progressBar.style.width = "50%";
                if (progressText) progressText.textContent = `Uploading ${assetFile.name}...`;

                const formData = new FormData();
                formData.append("seed", seed);
                formData.append("audience", audience);
                formData.append("competitors", competitors);
                formData.append("asset_first", "true");
                formData.append("asset_file", assetFile);

                const res = await fetch(API_RUN, {
                    method: "POST",
                    body: formData
                });

                const data = await res.json();
                if (!res.ok) throw new Error(data.error || "Failed to trigger pipeline");
                runId = data.run_id;

                if (progressBar) progressBar.style.width = "100%";
                if (progressText) progressText.textContent = "Upload complete!";
                
                // Hide progress, clear pending file
                setTimeout(() => {
                    if (progress) progress.classList.add("hidden");
                }, 1000);
                window.__pendingAssetFile = null;

            } else {
                // Standard mode - just create the run via JSON
                const res = await fetch(API_RUN, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ seed, audience, competitors })
                });

                const data = await res.json();
                if (!res.ok) throw new Error(data.error || "Failed to trigger pipeline");
                runId = data.run_id;
            }

            showToast("Video Factory run started successfully!");
            activeRunId = runId;
            selectedRunId = runId;

            // Update running display
            activeRunIndicator.classList.remove("hidden");
            activeRunIdDisplay.textContent = activeRunId;

            // Start log polling for this run
            startLogPolling(activeRunId);
            
            // Refresh runs list immediately
            loadRuns();

        } catch (err) {
            showToast(err.message, "error");
        } finally {
            triggerBtn.disabled = false;
        }
    }

    async function loadRuns() {
        try {
            const res = await fetch(API_RUNS);
            if (!res.ok) throw new Error("Could not fetch runs library");
            
            const runs = await res.json();
            runsList = runs;

            // Find if there is an active running job in the library to show indicators
            const activeRunningJob = runs.find(r => r.current_step !== "COMPLETED" && r.current_step !== "FAILED");
            if (activeRunningJob) {
                activeRunId = activeRunningJob.run_id;
                activeRunIndicator.classList.remove("hidden");
                activeRunIdDisplay.textContent = activeRunId;
                
            // If we aren't polling logs and this run is selected, poll it
                if (!logInterval && selectedRunId === activeRunId) {
                    startLogPolling(activeRunId);
                }
            } else {
                activeRunId = null;
                activeRunIndicator.classList.add("hidden");
            }

            // Populate selector dropdown in terminal
            updateTerminalSelector(runs);

            // Render list items
            renderRunsList(runs);

            // Update Shorts UI if selected run is active to show live generation progress
            if (selectedRunId) {
                const selectedRun = runs.find(r => r.run_id === selectedRunId);
                if (selectedRun) {
                    updateShortsUI(selectedRun);
                }
            }

        } catch (err) {
            console.error("Runs fetch failed", err);
        }
    }

    function updateTerminalSelector(runs) {
        // Keep current selected option value
        const currentSelection = logsSelector.value;
        
        // Reset selector
        logsSelector.innerHTML = '<option value="">-- Choose Historical Log --</option>';
        if (activeRunId) {
            logsSelector.innerHTML += `<option value="${activeRunId}">🟢 Live Active Run (${activeRunId})</option>`;
        }

        runs.forEach(run => {
            if (run.run_id !== activeRunId) {
                logsSelector.innerHTML += `<option value="${run.run_id}">${run.run_id} - ${run.topic_seed}</option>`;
            }
        });

        // Reapply selection if it still exists
        if (currentSelection && logsSelector.querySelector(`option[value="${currentSelection}"]`)) {
            logsSelector.value = currentSelection;
        }
    }

    function renderRunsList(runs) {
        if (runs.length === 0) {
            runsListContainer.innerHTML = '<li class="no-runs">No pipeline runs detected.</li>';
            return;
        }

        runsListContainer.innerHTML = "";
        runs.forEach(run => {
            const li = document.createElement("li");
            li.dataset.runId = run.run_id;
            if (selectedRunId === run.run_id) {
                li.classList.add("active");
            }

            // Format status tag
            let statusClass = "failed";
            let statusText = run.current_step;
            
            // Check if any step failed
            let hasFailed = false;
            if (run.steps) {
                for (let stepKey in run.steps) {
                    if (run.steps[stepKey].status === "FAILED") {
                        hasFailed = true;
                        break;
                    }
                }
            }

            if (hasFailed) {
                statusClass = "failed";
                statusText = "Failed";
            } else if (run.current_step === "COMPLETED") {
                statusClass = "success";
                statusText = "Ready";
            } else if (run.current_step !== "FAILED") {
                statusClass = "running";
                statusText = `In Progress (${run.current_step})`;
            }

            // Human readable timestamp from run_YYYYMMDD_HHMMSS
            let formattedTime = run.run_id;
            try {
                const parts = run.run_id.split("_");
                if (parts.length >= 3) {
                    const datePart = parts[1];
                    const timePart = parts[2];
                    const y = datePart.substring(0, 4);
                    const m = datePart.substring(4, 6);
                    const d = datePart.substring(6, 8);
                    const hh = timePart.substring(0, 2);
                    const mm = timePart.substring(2, 4);
                    formattedTime = `${y}-${m}-${d} ${hh}:${mm}`;
                }
            } catch (e) {}

            li.innerHTML = `
                <div class="run-title-row">
                    <span class="run-topic" title="${run.topic_seed}">${run.topic_seed}</span>
                    <div class="run-actions">
                        <span class="run-status-tag ${statusClass}">${statusText}</span>
                        <button class="btn-delete-run" title="Delete run logs and files">
                            <span class="material-symbols-outlined" style="font-size: 16px;">delete</span>
                        </button>
                    </div>
                </div>
                <div class="run-meta">${formattedTime} (${run.run_id})</div>
            `;

            li.addEventListener("click", () => handleRunSelection(run));
            
            const deleteBtn = li.querySelector(".btn-delete-run");
            deleteBtn.addEventListener("click", (e) => {
                e.stopPropagation(); // Prevent card selection when clicking delete
                if (confirm(`Are you sure you want to permanently delete run "${run.run_id}"? This will delete all its generated video files and logs from disk.`)) {
                    deleteRun(run.run_id);
                }
            });

            runsListContainer.appendChild(li);
        });
    }

    async function deleteRun(runId) {
        try {
            const res = await fetch(`/api/runs/${runId}`, {
                method: "DELETE"
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Delete failed");
            
            showToast("Run and associated files deleted successfully");
            
            // If the deleted run was the selected one, reset view
            if (selectedRunId === runId) {
                selectedRunId = null;
                logOutput.textContent = "Select a historical run or start a new run to view live log outputs.";
                logsSelector.value = "";
                hideVideo();
            }
            
            // Reload runs library
            loadRuns();
        } catch (err) {
            showToast("Failed to delete run: " + err.message, "error");
        }
    }


    function handleRunSelection(run) {
        selectedRunId = run.run_id;
        
        // Update active class in DOM
        document.querySelectorAll(".runs-list li").forEach(li => {
            if (li.dataset.runId === run.run_id) {
                li.classList.add("active");
            } else {
                li.classList.remove("active");
            }
        });

        // Set log selector dropdown value
        logsSelector.value = run.run_id;

        // Load logs
        loadLogs(run.run_id);
        
        // If run is running, poll logs. Otherwise, stop polling.
        const isRunning = run.current_step !== "COMPLETED" && run.current_step !== "FAILED";
        if (isRunning) {
            startLogPolling(run.run_id);
        } else {
            stopLogPolling();
        }

        // Handle video player load
        loadVideo(run.run_id, run.topic_seed);
        
        // Load run assets
        loadRunAssets(run.run_id);
    }

    // 4. Log management
    function startLogPolling(runId) {
        stopLogPolling();
        loadLogs(runId);
        logInterval = setInterval(() => {
            loadLogs(runId);
        }, 2000);
    }

    function stopLogPolling() {
        if (logInterval) {
            clearInterval(logInterval);
            logInterval = null;
        }
    }

    async function loadLogs(runId) {
        try {
            const res = await fetch(`${API_LOGS}${runId}`);
            if (!res.ok) throw new Error("Could not fetch log lines");
            const data = await res.json();
            
            logOutput.textContent = data.logs || "Log file empty.";
            
            // Scroll to bottom
            const terminal = document.querySelector(".terminal-body");
            terminal.scrollTop = terminal.scrollHeight;
        } catch (err) {
            logOutput.textContent = `Error loading logs: ${err.message}`;
        }
    }

    function handleLogSelection() {
        const val = logsSelector.value;
        if (!val) {
            selectedRunId = null;
            logOutput.textContent = "Select a run log to view.";
            stopLogPolling();
            return;
        }

        selectedRunId = val;
        
        // Find run details to see if it is running
        const run = runsList.find(r => r.run_id === val);
        const isRunning = run ? (run.current_step !== "COMPLETED" && run.current_step !== "FAILED") : false;

        if (isRunning) {
            startLogPolling(val);
        } else {
            stopLogPolling();
            loadLogs(val);
        }

        // Match side list select class
        document.querySelectorAll(".runs-list li").forEach(li => {
            if (li.dataset.runId === val) {
                li.classList.add("active");
                // Play video if it has video
                if (run && run.has_video) {
                    loadVideo(run.run_id, run.topic_seed);
                } else {
                    hideVideo();
                }
            } else {
                li.classList.remove("active");
            }
        });
    }

    // 5. Video Player controls
    function loadVideo(runId, title) {
        const run = runsList.find(r => r.run_id === runId);
        if (!run) return;

        videoPlaceholder.classList.add("hidden");
        videoWrapper.classList.remove("hidden");
        nowPlayingTitle.textContent = title;
        document.getElementById("now-playing-id").textContent = runId;

        // Set metadata card values
        document.getElementById("run-meta-audience").textContent = run.target_audience || "Default (General Audience)";
        document.getElementById("run-meta-competitors").textContent = run.competitor_analysis || "None";

        const resumeBtn = document.getElementById("btn-resume-run");
        if (resumeBtn) {
            if (run.current_step !== "COMPLETED" && run.current_step !== "CANCELLED") {
                resumeBtn.classList.remove("hidden");
            } else {
                resumeBtn.classList.add("hidden");
            }
        }

        const cancelBtn = document.getElementById("btn-cancel-run");
        if (cancelBtn) {
            if (run.current_step !== "COMPLETED" && run.current_step !== "CANCELLED" && run.current_step !== "FAILED") {
                cancelBtn.classList.remove("hidden");
            } else {
                cancelBtn.classList.add("hidden");
            }
        }

        // Set video subwrapper visibility and source
        const videoSubwrapper = document.getElementById("video-player-subwrapper");
        if (run.has_video) {
            videoSubwrapper.classList.remove("hidden");
            videoPlayer.src = `/videos/${runId}`;
            downloadVideoLink.href = `/videos/${runId}`;
            videoPlayer.load();
        } else {
            videoPlayer.pause();
            videoPlayer.src = "";
            videoSubwrapper.classList.add("hidden");
        }

        // Set thumbnail subwrapper visibility and source
        const thumbWrapper = document.getElementById("thumbnail-preview-wrapper");
        const downloadThumbLink = document.getElementById("download-thumbnail-link");

        if (run.has_thumbnail) {
            thumbWrapper.classList.remove("hidden");
            downloadThumbLink.href = `/thumbnails/${runId}`;
            // Load thumbnail variants
            loadThumbnailVariants(runId);
        } else {
            thumbWrapper.classList.add("hidden");
        }

        // Set guide link visibility
        const guideLink = document.getElementById("guide-link");
        if (guideLink) {
            // Check if guide exists by trying to fetch the metadata
            fetch(`/api/runs/${runId}/guide`)
                .then(res => {
                    if (res.ok) {
                        guideLink.href = `/guides/${runId}`;
                        guideLink.classList.remove("hidden");
                    } else {
                        guideLink.classList.add("hidden");
                    }
                })
                .catch(() => {
                    guideLink.classList.add("hidden");
                });
        }

        // Load community posts
        const communityWrapper = document.getElementById("community-posts-wrapper");
        if (communityWrapper) {
            communityWrapper.classList.remove("hidden");
            loadCommunityPosts(runId);
        }

        // Call vertical Shorts UI update
        updateShortsUI(run);
    }

    function updateShortsUI(run) {
        const shortWrapper = document.getElementById("short-preview-wrapper");
        const shortPlayerSubwrapper = document.getElementById("short-player-subwrapper");
        const shortPlayer = document.getElementById("short-player");
        const btnGenerateShort = document.getElementById("btn-generate-short");
        const btnUploadShort   = document.getElementById("btn-upload-short");
        const downloadShortLink = document.getElementById("download-short-link");
        const shortStatusInfo = document.getElementById("short-status-info");
        const shortStatusText = document.getElementById("short-status-text");

        // Short upload status elements
        const uploadWrapper = document.getElementById("short-upload-status-wrapper");
        const uploadLoader  = document.getElementById("short-upload-loader");
        const uploadDone    = document.getElementById("short-upload-done");
        const uploadFailed  = document.getElementById("short-upload-failed");
        const uploadLink    = document.getElementById("short-upload-link");
        const uploadErrTxt  = document.getElementById("short-upload-error-text");

        if (!shortWrapper) return;

        if (run.has_video) {
            shortWrapper.classList.remove("hidden");
            const runId = run.run_id;

            if (run.short_status === "SUCCESS" || run.has_short) {
                shortPlayerSubwrapper.classList.remove("hidden");
                const shortSrc = `/videos/${runId}/short`;
                if (!shortPlayer.src.includes(shortSrc)) {
                    shortPlayer.src = shortSrc;
                    shortPlayer.load();
                }
                downloadShortLink.href = `/videos/${runId}/short`;
                downloadShortLink.classList.remove("hidden");
                shortStatusInfo.classList.add("hidden");
                btnGenerateShort.classList.remove("hidden");
                btnGenerateShort.innerHTML = '<span class="material-symbols-outlined">auto_awesome</span> Regenerate 9:16 Short';
                btnGenerateShort.disabled = false;

                // --- Upload section ---
                if (btnUploadShort) {
                    const uploadStatus = run.short_upload_status || "NOT_STARTED";

                    if (uploadStatus === "UPLOADING") {
                        btnUploadShort.classList.add("hidden");
                        if (uploadWrapper) uploadWrapper.classList.remove("hidden");
                        if (uploadLoader)  uploadLoader.classList.remove("hidden");
                        if (uploadDone)    uploadDone.classList.add("hidden");
                        if (uploadFailed)  uploadFailed.classList.add("hidden");
                    } else if (uploadStatus === "SUCCESS" || uploadStatus === "SKIPPED_CREDENTIALS_MISSING") {
                        btnUploadShort.classList.remove("hidden");
                        btnUploadShort.disabled = false;
                        btnUploadShort.innerHTML = '<span class="material-symbols-outlined">rocket_launch</span> Re-upload Short';
                        if (uploadWrapper) uploadWrapper.classList.remove("hidden");
                        if (uploadLoader)  uploadLoader.classList.add("hidden");
                        if (uploadDone)    uploadDone.classList.remove("hidden");
                        if (uploadFailed)  uploadFailed.classList.add("hidden");
                        if (uploadLink && run.short_upload_url) {
                            uploadLink.href = run.short_upload_url;
                            uploadLink.textContent = run.short_upload_url;
                        }
                    } else if (uploadStatus === "FAILED") {
                        btnUploadShort.classList.remove("hidden");
                        btnUploadShort.disabled = false;
                        btnUploadShort.innerHTML = '<span class="material-symbols-outlined">rocket_launch</span> Retry Short Upload';
                        if (uploadWrapper) uploadWrapper.classList.remove("hidden");
                        if (uploadLoader)  uploadLoader.classList.add("hidden");
                        if (uploadDone)    uploadDone.classList.add("hidden");
                        if (uploadFailed)  uploadFailed.classList.remove("hidden");
                        if (uploadErrTxt)  uploadErrTxt.textContent = run.short_upload_error || "Upload failed.";
                    } else {
                        // NOT_STARTED — show upload button, hide status panel
                        btnUploadShort.classList.remove("hidden");
                        btnUploadShort.disabled = false;
                        btnUploadShort.innerHTML = '<span class="material-symbols-outlined">rocket_launch</span> Upload Short to YouTube';
                        if (uploadWrapper) uploadWrapper.classList.add("hidden");
                    }
                }
            } else if (run.short_status === "GENERATING") {
                shortPlayerSubwrapper.classList.add("hidden");
                downloadShortLink.classList.add("hidden");
                if (btnUploadShort) btnUploadShort.classList.add("hidden");
                if (uploadWrapper)  uploadWrapper.classList.add("hidden");
                shortStatusInfo.classList.remove("hidden");
                shortStatusText.textContent = "Generating Short in background (this may take up to a minute)...";
                btnGenerateShort.classList.add("hidden");
            } else if (run.short_status === "FAILED") {
                shortPlayerSubwrapper.classList.add("hidden");
                downloadShortLink.classList.add("hidden");
                if (btnUploadShort) btnUploadShort.classList.add("hidden");
                if (uploadWrapper)  uploadWrapper.classList.add("hidden");
                shortStatusInfo.classList.remove("hidden");
                shortStatusText.textContent = "Generation failed: " + (run.short_error || "Unknown error");
                btnGenerateShort.classList.remove("hidden");
                btnGenerateShort.innerHTML = '<span class="material-symbols-outlined">auto_awesome</span> Retry 9:16 Short';
                btnGenerateShort.disabled = false;
            } else {
                // NOT_STARTED
                shortPlayerSubwrapper.classList.add("hidden");
                downloadShortLink.classList.add("hidden");
                if (btnUploadShort) btnUploadShort.classList.add("hidden");
                if (uploadWrapper)  uploadWrapper.classList.add("hidden");
                shortStatusInfo.classList.add("hidden");
                btnGenerateShort.classList.remove("hidden");
                btnGenerateShort.innerHTML = '<span class="material-symbols-outlined">auto_awesome</span> Generate 9:16 Short';
                btnGenerateShort.disabled = false;
            }
        } else {
            shortWrapper.classList.add("hidden");
        }
    }


    function hideVideo() {
        videoPlayer.pause();
        videoPlayer.src = "";
        videoWrapper.classList.add("hidden");
        videoPlaceholder.classList.remove("hidden");

        const resumeBtn = document.getElementById("btn-resume-run");
        if (resumeBtn) {
            resumeBtn.classList.add("hidden");
        }

        const videoSubwrapper = document.getElementById("video-player-subwrapper");
        if (videoSubwrapper) {
            videoSubwrapper.classList.add("hidden");
        }

        const shortWrapper = document.getElementById("short-preview-wrapper");
        if (shortWrapper) {
            shortWrapper.classList.add("hidden");
            const shortPlayer = document.getElementById("short-player");
            if (shortPlayer) {
                shortPlayer.pause();
                shortPlayer.src = "";
            }
        }

        const thumbWrapper = document.getElementById("thumbnail-preview-wrapper");
        if (thumbWrapper) {
            thumbWrapper.classList.add("hidden");
        }
    }

    // Load and display thumbnail variants for A/B testing
    async function loadThumbnailVariants(runId) {
        try {
            const res = await fetch(`/api/runs/${runId}/thumbnail-variants`);
            if (!res.ok) return;
            const data = await res.json();
            const variants = data.variants || [];
            const selected = data.selected || "a";

            const labels = ["a", "b", "c"];
            labels.forEach((label, i) => {
                const card = document.getElementById(`variant-card-${label}`);
                const img = document.getElementById(`variant-img-${label}`);
                const textEl = document.getElementById(`variant-text-${label}`);
                const titleEl = document.getElementById(`variant-title-${label}`);
                if (!card || !img) return;

                const v = variants.find(x => x.label === label);
                if (v && v.has_thumbnail) {
                    img.src = `/thumbnails/${runId}/${label}`;
                    textEl.textContent = v.text_overlay || "";
                    titleEl.textContent = (v.title_suggestions && v.title_suggestions[0]) || "";
                    card.style.display = "";
                } else {
                    card.style.display = "none";
                }

                // Highlight selected
                if (label === selected) {
                    card.style.borderColor = "var(--accent-color)";
                    card.style.boxShadow = "0 0 8px rgba(99, 102, 241, 0.4)";
                } else {
                    card.style.borderColor = "var(--border-color)";
                    card.style.boxShadow = "none";
                }

                // Click handler to select variant
                card.onclick = async () => {
                    try {
                        const selRes = await fetch(`/api/runs/${runId}/select-thumbnail`, {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({variant: label})
                        });
                        if (selRes.ok) {
                            // Update UI highlight
                            labels.forEach(l => {
                                const c = document.getElementById(`variant-card-${l}`);
                                if (c) {
                                    c.style.borderColor = l === label ? "var(--accent-color)" : "var(--border-color)";
                                    c.style.boxShadow = l === label ? "0 0 8px rgba(99, 102, 241, 0.4)" : "none";
                                }
                            });
                            // Update download link
                            const dl = document.getElementById("download-thumbnail-link");
                            if (dl) dl.href = `/thumbnails/${runId}/${label}`;
                            showToast(`Thumbnail variant ${label.toUpperCase()} selected!`, "success");
                        }
                    } catch (e) {
                        showToast("Failed to select thumbnail", "error");
                    }
                };
            });
        } catch (e) {
            console.log("Failed to load thumbnail variants:", e);
        }
    }

    // Community Posts
    async function loadCommunityPosts(runId) {
        const wrapper = document.getElementById("community-posts-wrapper");
        const loading = document.getElementById("community-posts-loading");
        const empty = document.getElementById("community-posts-empty");
        const list = document.getElementById("community-posts-list");
        const btnGenerate = document.getElementById("btn-generate-posts");
        
        if (!wrapper) return;
        
        // Check if posts exist
        try {
            const res = await fetch(`/api/runs/${runId}/community-posts`);
            if (res.ok) {
                const posts = await res.json();
                displayCommunityPosts(posts, runId);
                return;
            }
        } catch (e) {}
        
        // No posts yet - show generate button
        empty.classList.remove("hidden");
        list.classList.add("hidden");
        
        btnGenerate.onclick = async () => {
            btnGenerate.disabled = true;
            loading.classList.remove("hidden");
            empty.classList.add("hidden");
            
            try {
                const res = await fetch(`/api/runs/${runId}/community-posts/generate`, {
                    method: "POST"
                });
                if (!res.ok) throw new Error("Failed to generate posts");
                const data = await res.json();
                displayCommunityPosts(data, runId);
            } catch (e) {
                showToast("Failed to generate community posts", "error");
                loading.classList.add("hidden");
                empty.classList.remove("hidden");
            }
            btnGenerate.disabled = false;
        };
    }

    function displayCommunityPosts(data, runId) {
        const loading = document.getElementById("community-posts-loading");
        const empty = document.getElementById("community-posts-empty");
        const list = document.getElementById("community-posts-list");
        
        loading.classList.add("hidden");
        empty.classList.add("hidden");
        list.classList.remove("hidden");
        
        const posts = data.posts || [];
        list.innerHTML = "";
        
        posts.forEach((post, index) => {
            const card = document.createElement("div");
            card.style.cssText = "background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 12px;";
            
            const typeColors = {teaser: "#6366f1", poll: "#22c55e", question: "#eab308", summary: "#3b82f6"};
            const typeColor = typeColors[post.type] || "#888";
            
            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <span style="font-size: 11px; text-transform: uppercase; color: ${typeColor}; font-weight: 600;">${post.type}</span>
                    <span style="font-size: 11px; color: #666;">${post.description || ""}</span>
                </div>
                <div style="font-size: 14px; color: var(--text-color); line-height: 1.6; white-space: pre-wrap; margin-bottom: 12px;">${post.text}</div>
                ${post.options ? `<div style="margin-bottom: 12px;">${post.options.map(o => `<div style="background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; font-size: 13px;">${o}</div>`).join("")}</div>` : ""}
                <div style="display: flex; gap: 8px;">
                    <button class="btn btn-secondary btn-copy-post" data-text="${encodeURIComponent(post.text)}" style="font-size: 12px;">
                        <span class="material-symbols-outlined" style="font-size: 16px;">content_copy</span> Copy
                    </button>
                </div>
            `;
            
            list.appendChild(card);
        });
        
        // Add copy handlers
        list.querySelectorAll(".btn-copy-post").forEach(btn => {
            btn.onclick = () => {
                const text = decodeURIComponent(btn.dataset.text);
                navigator.clipboard.writeText(text);
                showToast("Copied to clipboard!", "success");
            };
        });
    }


    // ==================== RUN ASSETS MANAGER ====================

    async function loadRunAssets(runId) {
        if (!assetsWrapper || !assetsList) return;
        
        try {
            const res = await fetch(`/api/runs/${runId}/assets`);
            if (!res.ok) throw new Error("Failed to load assets");
            const data = await res.json();
            renderAssetsList(data.assets || []);
            assetsWrapper.classList.remove("hidden");
        } catch (e) {
            console.warn("Could not load run assets:", e);
            assetsWrapper.classList.add("hidden");
        }
    }

    function renderAssetsList(assets) {
        if (!assetsList) return;
        assetsList.innerHTML = "";
        
        if (!assets || assets.length === 0) {
            assetsList.innerHTML = `<li style="font-size: 12px; color: var(--text-secondary); text-align: center; padding: 16px;">No assets uploaded yet. Drag a file above to add one.</li>`;
            return;
        }
        
        assets.forEach(a => {
            const li = document.createElement("li");
            li.style.cssText = "display: flex; align-items: center; gap: 12px; background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 10px 12px;";
            
            const isVideo = a.type === "video";
            const icon = isVideo ? "videocam" : "image";
            const sizeStr = `${a.size_mb} MB`;
            const tag = a.usage || `[Visual: asset:${isVideo ? "video" : "image"}:${a.path}]`;
            
            li.innerHTML = `
                <span class="material-symbols-outlined" style="color: var(--text-secondary); font-size: 24px;">${icon}</span>
                <div style="flex: 1; min-width: 0;">
                    <div style="font-size: 13px; font-weight: 500; color: var(--text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${a.original_name || a.filename}</div>
                    <div style="font-size: 11px; color: var(--text-secondary);">${sizeStr} | ${isVideo ? "Video" : "Image"}</div>
                    <div style="font-size: 10px; color: #666; margin-top: 2px; font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${tag}</div>
                </div>
                <button class="btn-copy-asset-tag btn btn-secondary btn-sm" data-tag="${tag}" style="font-size: 11px;">
                    <span class="material-symbols-outlined" style="font-size: 16px;">content_copy</span> Copy Tag
                </button>
                <button class="btn-delete-asset btn btn-danger btn-sm" data-filename="${a.filename}" style="font-size: 11px;">
                    <span class="material-symbols-outlined" style="font-size: 16px;">delete</span>
                </button>
            `;
            assetsList.appendChild(li);
        });
        
        // Add event listeners for copy/delete
        assetsList.querySelectorAll(".btn-copy-asset-tag").forEach(btn => {
            btn.onclick = async () => {
                const tag = btn.dataset.tag;
                await navigator.clipboard.writeText(tag);
                showToast("Asset tag copied! Paste in visual description.", "success");
            };
        });
        
        assetsList.querySelectorAll(".btn-delete-asset").forEach(btn => {
            btn.onclick = async () => {
                const filename = btn.dataset.filename;
                if (!confirm(`Delete asset "${filename}"?`)) return;
                await deleteAsset(selectedRunId, filename);
            };
        });
    }

    async function uploadAsset(runId, file) {
        if (!assetsUploadZone || !assetsProgress || !assetsProgressBar || !assetsProgressText) return;
        
        const formData = new FormData();
        formData.append("file", file);
        
        assetsUploadZone.style.borderColor = "var(--accent-color)";
        assetsProgress.classList.remove("hidden");
        assetsProgressBar.style.width = "0%";
        assetsProgressText.textContent = `Uploading ${file.name}...`;
        
        try {
            const xhr = new XMLHttpRequest();
            xhr.upload.addEventListener("progress", (e) => {
                if (e.lengthComputable) {
                    const percent = Math.round((e.loaded / e.total) * 100);
                    assetsProgressBar.style.width = percent + "%";
                    assetsProgressText.textContent = `Uploading ${file.name}... ${percent}%`;
                }
            });
            
            const result = await new Promise((resolve, reject) => {
                xhr.open("POST", `/api/runs/${runId}/assets`, true);
                xhr.onload = () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        resolve(JSON.parse(xhr.responseText));
                    } else {
                        reject(new Error(xhr.responseText || "Upload failed"));
                    }
                };
                xhr.onerror = () => reject(new Error("Network error"));
                xhr.send(formData);
            });
            
            assetsProgressBar.style.width = "100%";
            assetsProgressText.textContent = "Upload complete!";
            showToast(`Asset uploaded: ${result.asset.original_name}`, "success");
            
            // Reset and reload
            setTimeout(() => {
                assetsProgress.classList.add("hidden");
                assetsProgressBar.style.width = "0%";
                assetsUploadZone.style.borderColor = "var(--border-color)";
            }, 1500);
            
            loadRunAssets(runId);
            
        } catch (e) {
            assetsUploadZone.style.borderColor = "#ff5a5a";
            assetsProgressText.textContent = "Upload failed: " + e.message;
            showToast("Upload failed: " + e.message, "error");
            setTimeout(() => {
                assetsProgress.classList.add("hidden");
                assetsUploadZone.style.borderColor = "var(--border-color)";
            }, 3000);
        }
    }

    async function deleteAsset(runId, filename) {
        try {
            const res = await fetch(`/api/runs/${runId}/assets/${encodeURIComponent(filename)}`, {
                method: "DELETE"
            });
            if (!res.ok) throw new Error("Delete failed");
            showToast("Asset deleted", "success");
            loadRunAssets(runId);
        } catch (e) {
            showToast("Delete failed: " + e.message, "error");
        }
    }

    // Setup assets upload event listeners
    function initAssetsUpload() {
        if (!assetsUploadZone || !assetsFileInput) return;
        
        // Click to open file dialog
        assetsUploadZone.addEventListener("click", () => assetsFileInput.click());
        
        // Drag and drop
        assetsUploadZone.addEventListener("dragover", (e) => {
            e.preventDefault();
            assetsUploadZone.style.borderColor = "var(--accent-color)";
            assetsUploadZone.style.background = "rgba(99, 102, 241, 0.05)";
        });
        
        assetsUploadZone.addEventListener("dragleave", (e) => {
            e.preventDefault();
            assetsUploadZone.style.borderColor = "var(--border-color)";
            assetsUploadZone.style.background = "transparent";
        });
        
        assetsUploadZone.addEventListener("drop", (e) => {
            e.preventDefault();
            assetsUploadZone.style.borderColor = "var(--border-color)";
            assetsUploadZone.style.background = "transparent";
            
            const files = e.dataTransfer.files;
            if (files.length > 0 && selectedRunId) {
                uploadAsset(selectedRunId, files[0]);
            }
        });
        
        // File input change
        assetsFileInput.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file && selectedRunId) {
                uploadAsset(selectedRunId, file);
                assetsFileInput.value = ""; // Reset for same file re-upload
            }
        });
    }

        // ==================== END RUN ASSETS MANAGER ====================

    // Setup Asset-First mode for generator form
    function initAssetFirstMode() {
        const checkbox = document.getElementById("asset-first-mode");
        const uploadZone = document.getElementById("asset-upload-zone");
        const fileInput = document.getElementById("asset-file-input");
        const progress = document.getElementById("asset-upload-progress");
        const uploadedInfo = document.getElementById("asset-uploaded-info");
        const uploadedName = document.getElementById("asset-uploaded-name");
        const removeBtn = document.getElementById("asset-remove-btn");
        
        if (!checkbox || !uploadZone || !fileInput) return;
        
        // Initialize upload zone visibility based on checkbox state
        if (checkbox.checked) {
            uploadZone.classList.remove("hidden");
        } else {
            uploadZone.classList.add("hidden");
        }
        
        // Checkbox toggle
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
                uploadZone.classList.remove("hidden");
            } else {
                uploadZone.classList.add("hidden");
                progress.classList.add("hidden");
                fileInput.value = "";
            }
        });
        
        // Click to open file dialog
        uploadZone.addEventListener("click", () => fileInput.click());
        
        // Drag and drop
        uploadZone.addEventListener("dragover", (e) => {
            e.preventDefault();
            uploadZone.style.borderColor = "var(--accent-color)";
            uploadZone.style.background = "rgba(99, 102, 241, 0.05)";
        });
        
        uploadZone.addEventListener("dragleave", (e) => {
            e.preventDefault();
            uploadZone.style.borderColor = "var(--border-color)";
            uploadZone.style.background = "transparent";
        });
        
        uploadZone.addEventListener("drop", (e) => {
            e.preventDefault();
            uploadZone.style.borderColor = "var(--border-color)";
            uploadZone.style.background = "transparent";
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleAssetFileSelect(files[0]);
            }
        });
        
        // File input change
        fileInput.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file) handleAssetFileSelect(file);
        });
        
        // Handle file selection
        function handleAssetFileSelect(file) {
            // Validate file
            if (!file.name.match(/\.(mp4|mov|webm|mkv|avi)$/i)) {
                showToast("Invalid file type. Use MP4, MOV, WebM, MKV, or AVI.", "error");
                return;
            }
            if (file.size > 500 * 1024 * 1024) {
                showToast("File too large. Max 500MB.", "error");
                return;
            }
            
            // Store file for later upload
            window.__pendingAssetFile = file;
            
            // Instantly show the uploaded file details in the UI
            showUploadedInfo(file);
        }
        
        // Remove button
        if (removeBtn) {
            removeBtn.addEventListener("click", () => {
                window.__pendingAssetFile = null;
                document.getElementById("asset-uploaded-info").classList.add("hidden");
                document.getElementById("asset-upload-zone").classList.remove("hidden");
                document.getElementById("asset-file-input").value = "";
                document.getElementById("asset-first-mode").checked = false;
                document.getElementById("asset-upload-zone").classList.add("hidden");
            });
        }
        
        // Show uploaded info
        function showUploadedInfo(file) {
            document.getElementById("asset-upload-progress").classList.add("hidden");
            document.getElementById("asset-upload-zone").classList.add("hidden");
            document.getElementById("asset-uploaded-info").classList.remove("hidden");
            document.getElementById("asset-uploaded-name").textContent = 
                `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
        }
    }

    // ==================== END ASSET-FIRST MODE ====================
    const btnDiscoverTrends = document.getElementById("btn-discover-trends");
    const trendsLoading = document.getElementById("trends-loading-indicator");
    const trendsResults = document.getElementById("trends-results");
    
    // Newsjack elements
    const newsjackUrlInput = document.getElementById("newsjack-url");
    const btnNewsjackLink = document.getElementById("btn-newsjack-link");
    const newsjackLoading = document.getElementById("newsjack-loading-indicator");
    const newsjackRawText = document.getElementById("newsjack-raw-text");
    const btnNewsjackText = document.getElementById("btn-newsjack-text");
    const newsjackTabUrl = document.getElementById("newsjack-tab-url");
    const newsjackTabText = document.getElementById("newsjack-tab-text");
    const newsjackUrlPanel = document.getElementById("newsjack-url-panel");
    const newsjackTextPanel = document.getElementById("newsjack-text-panel");

    // Tab switcher logic
    function switchNewsjackTab(tab) {
        if (tab === "url") {
            newsjackUrlPanel.style.display = "flex";
            newsjackTextPanel.style.display = "none";
            newsjackTabUrl.style.background = "var(--accent-blue)";
            newsjackTabUrl.style.color = "#fff";
            newsjackTabText.style.background = "transparent";
            newsjackTabText.style.color = "var(--text-secondary)";
        } else {
            newsjackUrlPanel.style.display = "none";
            newsjackTextPanel.style.display = "flex";
            newsjackTabText.style.background = "var(--accent-blue)";
            newsjackTabText.style.color = "#fff";
            newsjackTabUrl.style.background = "transparent";
            newsjackTabUrl.style.color = "var(--text-secondary)";
        }
    }

    if (newsjackTabUrl) newsjackTabUrl.addEventListener("click", () => switchNewsjackTab("url"));
    if (newsjackTabText) newsjackTabText.addEventListener("click", () => switchNewsjackTab("text"));

    function renderTopicItem(topic, container) {
        const item = document.createElement("div");
        item.className = "trend-item";
        item.style.padding = "12px";
        item.style.border = "1px solid var(--border-color)";
        item.style.borderRadius = "8px";
        item.style.backgroundColor = "rgba(255, 255, 255, 0.01)";
        item.style.display = "flex";
        item.style.flexDirection = "column";
        item.style.gap = "6px";
        
        const titleEl = document.createElement("h4");
        titleEl.style.margin = "0";
        titleEl.style.fontSize = "13px";
        titleEl.style.color = "var(--accent-blue)";
        titleEl.textContent = topic.title;
        
        const pitchEl = document.createElement("p");
        pitchEl.style.margin = "0";
        pitchEl.style.fontSize = "11px";
        pitchEl.style.color = "var(--text-secondary)";
        pitchEl.textContent = topic.pitch;
        
        const pointsEl = document.createElement("div");
        pointsEl.style.fontSize = "10px";
        pointsEl.style.color = "#888888";
        pointsEl.innerHTML = `<strong>Talking points:</strong> ${topic.points ? topic.points.join(", ") : ""}`;
        
        const useBtn = document.createElement("button");
        useBtn.type = "button";
        useBtn.className = "btn btn-secondary";
        useBtn.style.padding = "6px 12px";
        useBtn.style.fontSize = "11px";
        useBtn.style.alignSelf = "flex-end";
        useBtn.style.marginTop = "4px";
        useBtn.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px; vertical-align:middle; margin-right:4px;">input</span> Use Topic`;
        
        useBtn.addEventListener("click", () => {
            const runSeedInput = document.getElementById("run-seed");
            const runAudienceInput = document.getElementById("run-audience");
            const runCompetitorsInput = document.getElementById("run-competitors");
            
            if (runSeedInput) runSeedInput.value = topic.seed || "";
            if (runAudienceInput) runAudienceInput.value = topic.target_audience || "";
            if (runCompetitorsInput) runCompetitorsInput.value = topic.competitor_analysis || "";
            
            showToast(`Concept fields populated for: "${topic.title}"`);
            document.querySelector(".card-generator").scrollIntoView({ behavior: "smooth" });
        });
        
        item.appendChild(titleEl);
        item.appendChild(pitchEl);
        item.appendChild(pointsEl);
        item.appendChild(useBtn);
        container.appendChild(item);
    }

    if (btnDiscoverTrends) {
        btnDiscoverTrends.addEventListener("click", async () => {
            btnDiscoverTrends.disabled = true;
            trendsLoading.classList.remove("hidden");
            trendsResults.innerHTML = "";
            
            try {
                const res = await fetch("/api/topics/trending");
                if (!res.ok) throw new Error("Failed to discover trending topics");
                const topics = await res.json();
                
                if (topics && topics.length > 0) {
                    topics.forEach(topic => {
                        renderTopicItem(topic, trendsResults);
                    });
                } else {
                    trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--text-secondary);">No trending topics found. Please verify your internet connection or API keys.</p>`;
                }
            } catch (err) {
                showToast("Failed to fetch trends: " + err.message, "error");
                trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--youtube-red);">Error: ${err.message}</p>`;
            } finally {
                trendsLoading.classList.add("hidden");
                btnDiscoverTrends.disabled = false;
            }
        });
    }

    if (btnNewsjackLink) {
        btnNewsjackLink.addEventListener("click", async () => {
            const newsjackUrl = newsjackUrlInput.value.trim();
            if (!newsjackUrl) {
                showToast("Please paste a valid news or tweet URL.", "warning");
                return;
            }

            btnNewsjackLink.disabled = true;
            newsjackLoading.classList.remove("hidden");
            trendsResults.innerHTML = "";

            try {
                const res = await fetch("/api/topics/newsjack", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ url: newsjackUrl })
                });
                
                const data = await res.json();
                
                if (!res.ok) {
                    // If backend says to use paste mode, auto-switch and show a helpful prompt
                    if (data.suggest_paste) {
                        switchNewsjackTab("text");
                        showToast("⚠️ " + (data.message || "Scraping failed — try Paste Text mode"), "warning");
                        trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--text-secondary); padding: 8px; background: rgba(255,180,0,0.05); border: 1px solid rgba(255,180,0,0.15); border-radius: 6px;">
                            ⚠️ ${data.message}<br><small style="opacity:0.7;">Copy the post text and paste it in the text box above, then click Synthesize.</small></p>`;
                    } else {
                        throw new Error(data.message || "Failed to process newsjack URL");
                    }
                    return;
                }

                if (data && data.length > 0) {
                    data.forEach(topic => {
                        renderTopicItem(topic, trendsResults);
                    });
                    showToast(`Successfully synthesized ${data.length} newsjacking topics!`);
                } else {
                    trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--text-secondary);">No topics could be synthesized. Try a different URL or use the Paste Text tab.</p>`;
                }
            } catch (err) {
                showToast("Newsjack Error: " + err.message, "error");
                trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--youtube-red);">Error: ${err.message}</p>`;
            } finally {
                newsjackLoading.classList.add("hidden");
                btnNewsjackLink.disabled = false;
            }
        });
    }

    // Newsjack text paste handler
    if (btnNewsjackText) {
        btnNewsjackText.addEventListener("click", async () => {
            const rawText = newsjackRawText ? newsjackRawText.value.trim() : "";
            if (!rawText || rawText.length < 30) {
                showToast("Please paste some content text first (at least a few sentences).", "warning");
                return;
            }

            btnNewsjackText.disabled = true;
            newsjackLoading.classList.remove("hidden");
            trendsResults.innerHTML = "";

            try {
                const res = await fetch("/api/topics/newsjack-text", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ text: rawText })
                });
                
                const data = await res.json();
                
                if (!res.ok) {
                    throw new Error(data.message || "Failed to synthesize topics from text");
                }

                if (data && data.length > 0) {
                    data.forEach(topic => {
                        renderTopicItem(topic, trendsResults);
                    });
                    showToast(`✅ Synthesized ${data.length} newsjacking topics from pasted text!`);
                } else {
                    trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--text-secondary);">No topics could be synthesized from the provided text.</p>`;
                }
            } catch (err) {
                showToast("Newsjack Error: " + err.message, "error");
                trendsResults.innerHTML = `<p style="font-size: 11px; text-align: center; color: var(--youtube-red);">Error: ${err.message}</p>`;
            } finally {
                newsjackLoading.classList.add("hidden");
                btnNewsjackText.disabled = false;
            }
        });
    }

    // ==================== ANALYTICS DASHBOARD ====================
    const refreshAnalyticsBtn = document.getElementById("refresh-analytics-btn");
    const analyticsLoading = document.getElementById("analytics-loading");

    function formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
        if (num >= 1000) return (num / 1000).toFixed(1) + "K";
        return num.toLocaleString();
    }

    function formatDuration(secs) {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        if (h > 0) return `${h}h ${m}m`;
        return `${m}m ${s}s`;
    }

    async function loadAnalytics() {
        if (analyticsLoading) analyticsLoading.classList.remove("hidden");

        try {
            const res = await fetch("/api/analytics");
            const data = await res.json();

            if (data.error) {
                showToast("Analytics error: " + data.error, "error");
                return;
            }

            // Update channel stats
            const ch = data.channel || {};
            const sm = data.summary || {};
            setTextSafe("stat-subs", formatNumber(ch.subscribers || 0));
            setTextSafe("stat-total-views", formatNumber(ch.total_views || 0));
            setTextSafe("stat-total-videos", ch.total_videos || 0);
            setTextSafe("stat-avg-views", formatNumber(sm.avg_views_per_video || 0));
            setTextSafe("stat-avg-engagement", (sm.avg_engagement_rate || 0) + "%");

            // Top performers table
            const topBody = document.getElementById("analytics-top-body");
            if (topBody && data.top_performers) {
                if (data.top_performers.length === 0) {
                    topBody.innerHTML = '<tr><td colspan="7" class="empty-row">No videos found</td></tr>';
                } else {
                    topBody.innerHTML = data.top_performers.map(v => `
                        <tr>
                            <td class="thumb-cell">
                                <img src="${v.thumbnail_url}" alt="thumb" loading="lazy" onerror="this.style.display='none'">
                            </td>
                            <td class="title-cell" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</td>
                            <td class="views-cell">${formatNumber(v.views)}</td>
                            <td>${formatNumber(v.likes)}</td>
                            <td>${formatNumber(v.comments)}</td>
                            <td class="engagement-cell">${v.engagement_rate}%</td>
                            <td>${v.published_at ? new Date(v.published_at).toLocaleDateString() : '--'}</td>
                        </tr>
                    `).join("");
                }
            }

            // Insights
            const pat = data.patterns || {};
            if (pat.top_title_words) {
                setTextSafe("insight-title-words", Object.keys(pat.top_title_words).join(", "));
            }
            if (pat.best_publish_days) {
                const days = Object.keys(pat.best_publish_days);
                setTextSafe("insight-best-day", days.length > 0 ? days[0] : "--");
            }
            if (pat.avg_duration_top5) {
                setTextSafe("insight-top-duration", formatDuration(pat.avg_duration_top5));
            }
            if (pat.top_tags_by_views) {
                setTextSafe("insight-top-tags", Object.keys(pat.top_tags_by_views).slice(0, 5).join(", "));
            }

            showToast("Analytics loaded");

        } catch (err) {
            showToast("Failed to load analytics: " + err.message, "error");
        } finally {
            if (analyticsLoading) analyticsLoading.classList.add("hidden");
        }
    }

    // Load deep analytics (watch time, traffic, demographics)
    async function loadDeepAnalytics() {
        try {
            // Watch time
            const wtRes = await fetch("/api/analytics/watch-time?days=365");
            const wtData = await wtRes.json();
            if (wtData.summary && !wtData.summary.error) {
                const s = wtData.summary;
                setTextSafe("deep-total-minutes", formatNumber(Math.round(s.estimatedMinutesWatched || 0)));
                setTextSafe("deep-avg-duration", formatDuration(Math.round(s.averageViewDuration || 0)));
                setTextSafe("deep-avg-pct", Math.round((s.averageViewPercentage || 0) * 10) / 10 + "%");
            } else if (wtData.summary && wtData.summary.error) {
                setTextSafe("deep-total-minutes", "Re-auth needed");
                setTextSafe("deep-avg-duration", "--");
                setTextSafe("deep-avg-pct", "--");
            }
        } catch (err) {
            console.log("Watch time analytics unavailable:", err.message);
        }

        try {
            // Traffic sources
            const tsRes = await fetch("/api/analytics/traffic-sources?days=365");
            const tsData = await tsRes.json();
            const trafficList = document.getElementById("traffic-list");
            if (trafficList && Array.isArray(tsData) && tsData.length > 0) {
                const maxTs = Math.max(...tsData.map(t => t.views || 0));
                trafficList.innerHTML = tsData.slice(0, 6).map(t => {
                    const name = t.insightTrafficSourceType || "Unknown";
                    const views = t.views || 0;
                    const pct = maxTs > 0 ? (views / maxTs * 100) : 0;
                    return `<div class="deep-list-item">
                        <span class="deep-list-label">${name}</span>
                        <span class="deep-list-value">${formatNumber(views)}</span>
                    </div>
                    <div class="deep-list-bar"><div class="deep-list-bar-fill" style="width:${pct}%"></div></div>`;
                }).join("");
            } else if (trafficList) {
                trafficList.innerHTML = '<span class="empty-row">No data (re-auth with Analytics scope)</span>';
            }
        } catch (err) {
            console.log("Traffic analytics unavailable:", err.message);
        }

        try {
            // Devices
            const devRes = await fetch("/api/analytics/devices?days=365");
            const devData = await devRes.json();
            const devicesList = document.getElementById("devices-list");
            if (devicesList && Array.isArray(devData) && devData.length > 0) {
                const maxDev = Math.max(...devData.map(d => d.views || 0));
                devicesList.innerHTML = devData.map(d => {
                    const name = d.deviceType || "Unknown";
                    const views = d.views || 0;
                    const pct = maxDev > 0 ? (views / maxDev * 100) : 0;
                    return `<div class="deep-list-item">
                        <span class="deep-list-label">${name}</span>
                        <span class="deep-list-value">${formatNumber(views)}</span>
                    </div>
                    <div class="deep-list-bar"><div class="deep-list-bar-fill" style="width:${pct}%"></div></div>`;
                }).join("");
            } else if (devicesList) {
                devicesList.innerHTML = '<span class="empty-row">No data (re-auth with Analytics scope)</span>';
            }
        } catch (err) {
            console.log("Device analytics unavailable:", err.message);
        }

        try {
            // Demographics
            const demoRes = await fetch("/api/analytics/demographics?days=365");
            const demoData = await demoRes.json();
            const demoList = document.getElementById("demographics-list");
            if (demoList) {
                let html = "";
                if (demoData.genders && demoData.genders.length > 0) {
                    html += '<div style="margin-bottom:8px"><strong style="font-size:0.75rem;color:var(--text-secondary)">Gender</strong></div>';
                    demoData.genders.forEach(g => {
                        const name = g.gender || "Unknown";
                        const pct = g.viewerPercentage || 0;
                        html += `<div class="deep-list-item">
                            <span class="deep-list-label">${name}</span>
                            <span class="deep-list-value">${Math.round(pct * 10) / 10}%</span>
                        </div>
                        <div class="deep-list-bar"><div class="deep-list-bar-fill" style="width:${pct}%"></div></div>`;
                    });
                }
                if (demoData.age_groups && demoData.age_groups.length > 0) {
                    html += '<div style="margin:8px 0 4px"><strong style="font-size:0.75rem;color:var(--text-secondary)">Age Groups</strong></div>';
                    demoData.age_groups.forEach(a => {
                        const name = a.ageGroup || "Unknown";
                        const pct = a.viewerPercentage || 0;
                        html += `<div class="deep-list-item">
                            <span class="deep-list-label">${name}</span>
                            <span class="deep-list-value">${Math.round(pct * 10) / 10}%</span>
                        </div>
                        <div class="deep-list-bar"><div class="deep-list-bar-fill" style="width:${pct}%"></div></div>`;
                    });
                }
                demoList.innerHTML = html || '<span class="empty-row">No data (re-auth with Analytics scope)</span>';
            }
        } catch (err) {
            console.log("Demographics analytics unavailable:", err.message);
        }
    }

    function setTextSafe(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    if (refreshAnalyticsBtn) {
        refreshAnalyticsBtn.addEventListener("click", () => {
            loadAnalytics();
            loadDeepAnalytics();
        });
    }

    // Auto-load analytics on page load
    loadAnalytics();
    loadDeepAnalytics();

    // ==================== COMMENT AUTO-RESPONSE ====================
    const fetchCommentsBtn = document.getElementById("fetch-comments-btn");
    const autoReplyBtn = document.getElementById("auto-reply-btn");
    const commentsLoading = document.getElementById("comments-loading");
    const commentsList = document.getElementById("comments-list");
    const commentsActions = document.getElementById("comments-actions");
    const commentsDays = document.getElementById("comments-days");

    let currentComments = [];

    async function fetchComments() {
        if (commentsLoading) commentsLoading.classList.remove("hidden");
        if (fetchCommentsBtn) fetchCommentsBtn.disabled = true;

        try {
            const days = commentsDays ? commentsDays.value : 7;
            const res = await fetch(`/api/comments?days=${days}`);
            const comments = await res.json();

            if (comments.error) {
                showToast("Error: " + comments.error, "error");
                return;
            }

            currentComments = comments;

            // Update stats
            setTextSafe("stat-total-comments", comments.length);
            const unreplied = comments.filter(c => !c.has_reply);
            setTextSafe("stat-unreplied", unreplied.length);
            setTextSafe("stat-replies-generated", "0");

            // Render comments
            if (commentsList) {
                if (comments.length === 0) {
                    commentsList.innerHTML = '<span class="empty-row">No recent comments found</span>';
                } else {
                    commentsList.innerHTML = comments.map(c => `
                        <div class="comment-item" data-comment-id="${c.comment_id}">
                            <div class="comment-header">
                                <span class="comment-author">${escapeHtml(c.author)}</span>
                                <span class="comment-video">${escapeHtml(c.video_title || '')}</span>
                            </div>
                            <div class="comment-text">${escapeHtml(c.text)}</div>
                            <div class="comment-meta">
                                <span>${c.likes || 0} likes</span>
                                <span>${new Date(c.published_at).toLocaleDateString()}</span>
                                <span>${c.has_reply ? 'Has reply' : 'No reply yet'}</span>
                            </div>
                            <div class="comment-reply-box" id="reply-box-${c.comment_id}" style="display:none">
                                <span class="comment-reply-label">AI Reply</span>
                                <div class="comment-reply-text" id="reply-text-${c.comment_id}"></div>
                            </div>
                        </div>
                    `).join("");
                }
            }

            // Show actions if there are unreplied comments
            if (commentsActions && unreplied.length > 0) {
                commentsActions.classList.remove("hidden");
            }

            showToast(`Found ${comments.length} comments (${unreplied.length} unreplied)`);

        } catch (err) {
            showToast("Failed to fetch comments: " + err.message, "error");
        } finally {
            if (commentsLoading) commentsLoading.classList.add("hidden");
            if (fetchCommentsBtn) fetchCommentsBtn.disabled = false;
        }
    }

    async function autoReply() {
        if (commentsLoading) commentsLoading.classList.remove("hidden");
        if (autoReplyBtn) autoReplyBtn.disabled = true;

        try {
            const dryRun = document.getElementById("dry-run-toggle")?.checked ?? true;
            const maxReplies = document.getElementById("reply-count")?.value || 5;

            const res = await fetch("/api/comments/auto-reply", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({max_replies: parseInt(maxReplies), dry_run: dryRun})
            });
            const data = await res.json();

            if (data.error) {
                showToast("Error: " + data.error, "error");
                return;
            }

            setTextSafe("stat-replies-generated", data.replies.length);

            // Update UI with replies
            for (const reply of data.replies) {
                const replyBox = document.getElementById(`reply-box-${reply.comment_id}`);
                const replyText = document.getElementById(`reply-text-${reply.comment_id}`);

                if (replyBox && replyText) {
                    replyBox.style.display = "block";
                    replyText.textContent = reply.reply_text || "Failed to generate";
                }
            }

            const mode = dryRun ? "Preview" : "Posted";
            showToast(`${mode}: ${data.replies.length} replies generated`);

        } catch (err) {
            showToast("Auto-reply failed: " + err.message, "error");
        } finally {
            if (commentsLoading) commentsLoading.classList.add("hidden");
            if (autoReplyBtn) autoReplyBtn.disabled = false;
        }
    }

    if (fetchCommentsBtn) {
        fetchCommentsBtn.addEventListener("click", fetchComments);
    }

    if (autoReplyBtn) {
        autoReplyBtn.addEventListener("click", autoReply);
    }

    // Website Scraper
    const scrapeBtn = document.getElementById("scrape-btn");
    const scrapeUrl = document.getElementById("scrape-url");
    const scrapeMaxPages = document.getElementById("scrape-max-pages");
    const scrapeLoading = document.getElementById("scrape-loading");
    const scrapeSummary = document.getElementById("scrape-summary");
    const scrapePages = document.getElementById("scrape-pages");
    const scrapeNotes = document.getElementById("scrape-notes");

    async function scrapeWebsite() {
        const url = scrapeUrl?.value?.trim();
        if (!url) {
            showToast("Enter a URL to scrape", "error");
            return;
        }
        const maxPages = parseInt(scrapeMaxPages?.value) || 10;
        const notes = scrapeNotes?.value?.trim() || "";

        if (scrapeLoading) scrapeLoading.classList.remove("hidden");
        if (scrapeBtn) scrapeBtn.disabled = true;

        try {
            const res = await fetch("/api/scrape", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, max_pages: maxPages, notes }),
            });
            const text = await res.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (e) {
                showToast("Server returned non-JSON. Restart the Flask server to load the scraper route.", "error");
                return;
            }

            if (data.error) {
                showToast("Scrape failed: " + data.error, "error");
                return;
            }

            // Update summary
            if (scrapeSummary) {
                scrapeSummary.classList.remove("hidden");
                setTextSafe("stat-scraped-pages", data.total_pages || 0);
                setTextSafe("stat-scraped-words", (data.total_words || 0).toLocaleString());
                setTextSafe("stat-scraped-errors", data.errors?.length || 0);
            }

            // Update pages list
            if (scrapePages) {
                if (data.pages && data.pages.length > 0) {
                    let html = "";
                    for (const page of data.pages) {
                        const title = page.title || "Untitled";
                        const preview = (page.body || "").substring(0, 200);
                        const words = page.word_count || 0;
                        html += `
                            <div class="scrape-page-item">
                                <div class="scrape-page-header">
                                    <strong>${title}</strong>
                                    <span class="scrape-page-words">${words} words</span>
                                </div>
                                <div class="scrape-page-url">${page.url || ""}</div>
                                <div class="scrape-page-preview">${preview}...</div>
                                <div class="scrape-page-images">${page.images?.length || 0} images</div>
                            </div>`;
                    }
                    scrapePages.innerHTML = html;
                } else {
                    scrapePages.innerHTML = '<span class="empty-row">No content extracted</span>';
                }
            }

            showToast(`Scraped ${data.total_pages} pages (${data.total_words} words) — saved for pipeline`);
        } catch (err) {
            showToast("Scrape failed: " + err.message, "error");
        } finally {
            if (scrapeLoading) scrapeLoading.classList.add("hidden");
            if (scrapeBtn) scrapeBtn.disabled = false;
        }
    }

    async function clearScrapedData() {
        try {
            const res = await fetch("/api/scrape/clear", { method: "POST" });
            const data = await res.json();
            if (scrapeSummary) scrapeSummary.classList.add("hidden");
            if (scrapePages) scrapePages.innerHTML = '<span class="empty-row">Enter a URL and click Scrape to extract website content</span>';
            showToast("Scraped data cleared");
        } catch (err) {
            showToast("Clear failed: " + err.message, "error");
        }
    }

    async function checkScrapeStatus() {
        try {
            const res = await fetch("/api/scrape/status");
            const data = await res.json();
            const scrapeHint = document.getElementById("scrape-hint");
            if (data.has_data) {
                if (scrapeSummary) {
                    scrapeSummary.classList.remove("hidden");
                    setTextSafe("stat-scraped-pages", data.total_pages || 0);
                    setTextSafe("stat-scraped-words", (data.total_words || 0).toLocaleString());
                    setTextSafe("stat-scraped-errors", 0);
                }
                if (scrapeUrl && data.base_url) scrapeUrl.value = data.base_url;
                if (scrapeHint) scrapeHint.classList.remove("hidden");
            } else {
                if (scrapeHint) scrapeHint.classList.add("hidden");
            }
        } catch (err) { /* ignore */ }
    }

    const scrapeClearBtn = document.getElementById("scrape-clear-btn");
    if (scrapeBtn) {
        scrapeBtn.addEventListener("click", scrapeWebsite);
    }
    if (scrapeClearBtn) {
        scrapeClearBtn.addEventListener("click", clearScrapedData);
    }

    // Check for saved scraped data on page load
    checkScrapeStatus();
    }
});
