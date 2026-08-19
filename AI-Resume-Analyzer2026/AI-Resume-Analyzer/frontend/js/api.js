// URL du backend — définie globalement dans index.html (window.BACKEND_URL)
const API_BASE_URL = (window.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '') + '/api';

/**
 * Reads an error message out of a failed response, without throwing if the
 * body isn't valid JSON (e.g. a raw 500 error, a proxy error page, etc.).
 */
async function readErrorDetail(response, fallback) {
    try {
        const errorData = await response.json();
        return errorData.detail || fallback;
    } catch (e) {
        return fallback;
    }
}

/**
 * Ces routes exigent désormais d'être connecté (voir require_auth() côté
 * backend) — on ajoute le jeton de session (window.AUTH_TOKEN, posé par
 * showDashboard() dans index.html après connexion) sur chaque appel.
 */
function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (window.AUTH_TOKEN) {
        headers['Authorization'] = 'Bearer ' + window.AUTH_TOKEN;
    }
    return headers;
}

const ApiService = {
    /**
     * Uploads and analyzes a resume file.
     * @param {File} file - PDF or DOCX file
     * @param {string} [jobDescription] - Optional job description to match against
     */
    async analyzeResume(file, jobDescription = '') {
        const formData = new FormData();
        formData.append('file', file);
        if (jobDescription.trim()) {
            formData.append('job_description', jobDescription);
        }

        try {
            const response = await fetch(`${API_BASE_URL}/analyze`, {
                method: 'POST',
                headers: authHeaders(),
                body: formData, // Fetch automatically sets Content-Type to multipart/form-data
            });

            if (!response.ok) {
                const detail = await readErrorDetail(response, 'Failed to analyze the resume.');
                throw new Error(detail);
            }

            return await response.json();
        } catch (error) {
            console.error('API Error (analyzeResume):', error);
            throw error;
        }
    },

    /**
     * Fetches all past analyses summaries.
     * Adds a default 'completed' status since the backend doesn't persist
     * one (every stored analysis finished successfully by definition).
     */
    async getHistory() {
        try {
            const response = await fetch(`${API_BASE_URL}/history`, { headers: authHeaders() });
            if (!response.ok) {
                const detail = await readErrorDetail(response, 'Failed to fetch analysis history.');
                throw new Error(detail);
            }
            const list = await response.json();
            return list.map(item => ({ ...item, status: item.status || 'completed' }));
        } catch (error) {
            console.error('API Error (getHistory):', error);
            throw error;
        }
    },

    /**
     * Fetches Dashboard KPIs.
     * Maps the backend's field names (total_resumes, total_analyses,
     * average_score, average_match) to the names app_v3.js expects
     * (total_resumes, analyzed_resumes, average_ats_score, average_match_score).
     */
    async getDashboardKPIs() {
        try {
            const response = await fetch(`${API_BASE_URL}/dashboard/kpis`, { headers: authHeaders() });
            if (!response.ok) {
                const detail = await readErrorDetail(response, 'Failed to fetch Dashboard KPIs.');
                throw new Error(detail);
            }
            const data = await response.json();
            return {
                total_resumes: data.total_resumes ?? 0,
                analyzed_resumes: data.analyzed_resumes ?? data.total_analyses ?? 0,
                average_ats_score: data.average_ats_score ?? data.average_score ?? 0,
                average_match_score: data.average_match_score ?? data.average_match ?? 0,
            };
        } catch (error) {
            console.error('API Error (getDashboardKPIs):', error);
            throw error;
        }
    },

    /**
     * Fetches a detailed analysis by its database ID.
     * @param {number} analysisId
     */
    async getAnalysisDetail(analysisId) {
        try {
            const response = await fetch(`${API_BASE_URL}/history/${analysisId}`, { headers: authHeaders() });
            if (!response.ok) {
                const detail = await readErrorDetail(response, 'Failed to fetch detailed analysis.');
                throw new Error(detail);
            }
            return await response.json();
        } catch (error) {
            console.error('API Error (getAnalysisDetail):', error);
            throw error;
        }
    },

    /**
     * Deletes an analysis record.
     * @param {number} analysisId
     */
    async deleteAnalysis(analysisId) {
        try {
            const response = await fetch(`${API_BASE_URL}/history/${analysisId}`, {
                method: 'DELETE',
                headers: authHeaders()
            });
            if (!response.ok) {
                if (response.status === 404) {
                    return { message: 'Déjà supprimée', id: analysisId };
                }
                const detail = await readErrorDetail(response, 'Failed to delete the analysis record.');
                throw new Error(detail);
            }
            return await response.json();
        } catch (error) {
            console.error('API Error (deleteAnalysis):', error);
            throw error;
        }
    },

    /**
     * Sends an interactive chat message about a resume.
     * @param {number} analysisId
     * @param {string} message
     */
    async chatWithResume(analysisId, message) {
        try {
            const response = await fetch(`${API_BASE_URL}/history/${analysisId}/chat`, {
                method: 'POST',
                headers: authHeaders({
                    'Content-Type': 'application/json'
                }),
                body: JSON.stringify({ message })
            });

            if (!response.ok) {
                const detail = await readErrorDetail(response, 'Failed to get chat response.');
                throw new Error(detail);
            }

            return await response.json();
        } catch (error) {
            console.error('API Error (chatWithResume):', error);
            throw error;
        }
    }
};
