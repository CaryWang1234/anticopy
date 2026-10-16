/**
 * AntiCopy - 作业抄袭检测系统前端逻辑
 *
 * 检测模式：
 *   text     文本比对（标准答案 vs 多份学生答案）
 *   pairwise 学生互查（两两比对 + 团伙聚类，不需要标准答案）
 *   paper    整卷多题（逐题比对后按分值汇总）
 *   aigc     作文 AI 风格线索（本地可解释特征，外部判别需显式勾选）
 */

// 当前结果面板要导出/筛选的数据
const state = {
    mode: 'text',
    results: [],
    statistics: {},
    standardAnswer: '',
    timestamp: '',
    pairwise: null,     // {students, matrix, groups, per_student, pairs}
    paper: null,        // {questions, per_question, rollup, assignment_name}
    aigc: [],           // AIGC 结果不参与相似度导出
    aigcEssays: [],     // 送检原文：证据高亮要用，结果里只回下标
};

// 请求超时（毫秒）：大模型接口可能较慢，本地检测不会用到这个上限
const REQUEST_TIMEOUT = 20000;
const LLM_TIMEOUT = 90000;

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initModeSelector();

    // 文本 / 互查两条路径的学生卡片
    initStudentCards('student-count', 'student-cards', 'generate-cards', 'decrease-count', 'increase-count');
    initStudentCards('student-count-pair', 'student-cards-pair', 'generate-cards-pair', 'decrease-count-pair', 'increase-count-pair', 2);

    initDetection();
    initAnswerHelper();
    initAigcPanel();
    initPairwisePanel();
    initPaperPanel();
    initResultToolbar();
    initSettings();
    initActions();
    initBatchImport();
    initFileUpload();
    loadHistory();

    // 默认生成卡片
    generateCards('student-cards', 3);
    generateCards('student-cards-pair', 3);
});

// ==================== 导航切换 ====================

function initNavigation() {
    const allLinks = document.querySelectorAll('.nav-link, .tab-bar-item');

    allLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const tab = link.dataset.tab;

            // 同步顶部导航和底部 Tab Bar
            document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
            document.querySelectorAll('.tab-bar-item').forEach(l => l.classList.remove('active'));
            document.querySelectorAll(`[data-tab="${tab}"]`).forEach(l => l.classList.add('active'));

            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(`tab-${tab}`);
            if (panel) panel.classList.add('active');

            // 隐藏结果面板
            document.getElementById('result-panel').style.display = 'none';

            // 切换到历史时刷新
            if (tab === 'history') loadHistory();
        });
    });
}

// ==================== 模式切换 ====================

function initModeSelector() {
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.dataset.mode;

            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            document.querySelectorAll('.detect-form').forEach(f => f.classList.remove('active'));
            const form = document.getElementById(`form-${mode}`);
            if (form) form.classList.add('active');
        });
    });
}

function switchToDetectTab() {
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.tab-bar-item').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('[data-tab="detect"]').forEach(l => l.classList.add('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-detect').classList.add('active');
}

// ==================== 学生卡片生成 ====================

function initStudentCards(countId, cardsId, btnId, decreaseId, increaseId, min = 1) {
    const countInput = document.getElementById(countId);
    const decreaseBtn = document.getElementById(decreaseId);
    const increaseBtn = document.getElementById(increaseId);
    const generateBtn = document.getElementById(btnId);

    if (!countInput) return;

    decreaseBtn.addEventListener('click', () => {
        let val = parseInt(countInput.value) || min;
        if (val > min) countInput.value = val - 1;
    });

    increaseBtn.addEventListener('click', () => {
        let val = parseInt(countInput.value) || min;
        if (val < 50) countInput.value = val + 1;
    });

    generateBtn.addEventListener('click', () => {
        const count = Math.max(min, parseInt(countInput.value) || min);
        generateCards(cardsId, count);
    });
}

function generateCards(containerId, count) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '';
    for (let i = 0; i < count; i++) {
        container.appendChild(createStudentCard(i + 1));
    }
}

/**
 * 创建学生卡片（姓名 + 答案文本 + 多张拍照）
 */
function createStudentCard(index, name = '', text = '', photos = []) {
    const card = document.createElement('div');
    card.className = 'student-card';

    // 顶行：序号 + 姓名 + 拍照按钮 + 删除卡片
    const topRow = document.createElement('div');
    topRow.className = 'card-top-row';

    const idx = document.createElement('span');
    idx.className = 'card-index';
    idx.textContent = String(index);

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'student-name-input';
    nameInput.placeholder = `学生${index}`;
    nameInput.value = name;

    const photoLabel = document.createElement('label');
    photoLabel.className = 'card-photo-btn';
    photoLabel.title = '拍照或从相册选择（可多张）';
    photoLabel.innerHTML = '<span class="btn-icon">📷</span><span class="photo-count">拍照</span>';
    const photoInput = document.createElement('input');
    photoInput.type = 'file';
    photoInput.accept = 'image/*';
    photoInput.multiple = true;
    photoInput.setAttribute('capture', 'environment'); // 手机端直接调起后置相机
    photoInput.className = 'btn-upload-input';
    photoLabel.appendChild(photoInput);
    photoInput.addEventListener('change', async () => {
        if (!photoInput.files.length) return;
        await uploadCardPhotos(card, photoInput.files);
        photoInput.value = '';
    });

    const removeBtn = document.createElement('button');
    removeBtn.className = 'card-remove';
    removeBtn.title = '删除该学生';
    removeBtn.setAttribute('aria-label', '删除该学生');
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => {
        card.remove();
        renumberCards(card.parentElement);
    });

    topRow.appendChild(idx);
    topRow.appendChild(nameInput);
    topRow.appendChild(photoLabel);
    topRow.appendChild(removeBtn);

    // 答案文本框（可选，有照片即可参与记录）
    const textInput = document.createElement('textarea');
    textInput.className = 'student-text-input';
    textInput.rows = 1;
    textInput.placeholder = '答案文字（可选，已拍照可留空）';
    textInput.value = text;

    // 照片缩略图区
    const photoBox = document.createElement('div');
    photoBox.className = 'card-photos';

    card.appendChild(topRow);
    card.appendChild(textInput);
    card.appendChild(photoBox);

    photos.forEach(url => addPhotoThumb(card, url));
    return card;
}

/** 重新编号卡片序号 */
function renumberCards(container) {
    if (!container) return;
    container.querySelectorAll('.student-card').forEach((card, i) => {
        const indexEl = card.querySelector('.card-index');
        if (indexEl) indexEl.textContent = String(i + 1);
        const nameInput = card.querySelector('.student-name-input');
        if (nameInput) nameInput.placeholder = `学生${i + 1}`;
    });
}

/** 更新卡片拍照按钮上的照片数量 */
function updatePhotoCount(card) {
    const n = card.querySelectorAll('.photo-thumb').length;
    card.querySelector('.photo-count').textContent = n > 0 ? `${n}张` : '拍照';
}

/** 添加一张照片缩略图（带删除） */
function addPhotoThumb(card, url) {
    const photoBox = card.querySelector('.card-photos');
    const thumb = document.createElement('div');
    thumb.className = 'photo-thumb';
    thumb.dataset.url = url;

    const img = document.createElement('img');
    img.src = url;
    img.alt = '答案照片';
    img.addEventListener('click', () => window.open(url, '_blank'));

    const del = document.createElement('button');
    del.className = 'photo-remove';
    del.textContent = '×';
    del.setAttribute('aria-label', '删除这张照片');
    del.addEventListener('click', () => {
        thumb.remove();
        updatePhotoCount(card);
    });

    thumb.appendChild(img);
    thumb.appendChild(del);
    photoBox.appendChild(thumb);
    updatePhotoCount(card);
}

/** 上传卡片选中的照片 */
async function uploadCardPhotos(card, files) {
    showLoading(true);
    try {
        const formData = new FormData();
        Array.from(files).forEach(f => formData.append('images', f));

        const resp = await fetch('/api/upload/answer-images', { method: 'POST', body: formData });
        const data = await resp.json();

        if (data.success) {
            data.urls.forEach(url => addPhotoThumb(card, url));
            showToast(`已添加 ${data.urls.length} 张照片`, 'success');
        } else {
            showToast(data.error || '上传失败', 'error');
        }
    } catch (e) {
        showToast('上传失败: ' + e.message, 'error');
    } finally {
        showLoading(false);
    }
}

function collectStudents(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];

    const cards = container.querySelectorAll('.student-card');
    const students = [];

    cards.forEach((card, i) => {
        const name = card.querySelector('.student-name-input').value.trim() || `学生${i + 1}`;
        const text = card.querySelector('.student-text-input').value.trim();
        const photos = Array.from(card.querySelectorAll('.photo-thumb')).map(t => t.dataset.url);
        if (text || photos.length > 0) {
            students.push({ name, text, photos });
        }
    });

    return students;
}

// ==================== 检测逻辑 ====================

function initDetection() {
    // 文本检测
    document.getElementById('btn-detect-text').addEventListener('click', async () => {
        const standard = document.getElementById('standard-answer').value.trim();
        const students = collectStudents('student-cards');

        if (!standard) {
            showToast('请输入标准答案', 'error');
            return;
        }
        if (!validateStudents(students)) return;

        await detectText(standard, students);
    });
}

/** 检测前校验学生数据；仅照片无文字的学生不参与相似度计算 */
function validateStudents(students) {
    if (students.length === 0) {
        showToast('请至少填写一份学生答案', 'error');
        return false;
    }
    const photoOnly = students.filter(s => !s.text).length;
    if (photoOnly === students.length) {
        showToast('学生都只有照片，相似度检测需要答案文字', 'error');
        return false;
    }
    if (photoOnly > 0) {
        showToast(`${photoOnly} 名学生仅有照片，未参与本次检测`, 'info');
    }
    return true;
}

async function detectText(standard, students) {
    showLoading(true);
    try {
        const resp = await fetchWithTimeout('/api/detect/text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ standard_answer: standard, students })
        });
        const data = await resp.json();
        if (data.success) {
            data.mode = 'text';
            renderResults(data);
            showToast('检测完成！', 'success');
        } else {
            showToast(data.error || '检测失败', 'error');
        }
    } catch (err) {
        showToast(describeFetchError(err), 'error');
    }
    showLoading(false);
}

// ==================== AI 生成参考答案 ====================

function initAnswerHelper() {
    const btn = document.getElementById('btn-generate-answer');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const question = document.getElementById('helper-question').value.trim();
        if (!question) {
            showToast('先把题目粘贴进来', 'error');
            return;
        }

        const box = document.getElementById('helper-result');
        box.style.display = 'block';
        box.textContent = '正在生成，请稍候…';
        btn.disabled = true;

        try {
            const resp = await fetchWithTimeout('/api/answer/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question: question,
                    subject: document.getElementById('helper-subject').value.trim(),
                    grade: document.getElementById('helper-grade').value.trim(),
                    answer_style: document.getElementById('helper-style').value,
                })
            }, LLM_TIMEOUT);
            const data = await resp.json();
            renderAnswerHelper(data, box);
        } catch (err) {
            box.textContent = describeFetchError(err);
        }
        btn.disabled = false;
    });
}

/** 生成结果只能「填进」标准答案框，不能自动填——老师得先看一眼 */
function renderAnswerHelper(data, box) {
    box.innerHTML = '';

    if (!data.success) {
        const error = document.createElement('div');
        error.className = 'helper-error';
        error.textContent = data.error || '生成失败';
        box.appendChild(error);
        if (data.hint) {
            const hint = document.createElement('div');
            hint.className = 'helper-hint-text';
            hint.textContent = data.hint;
            box.appendChild(hint);
        }
        return;
    }

    const notice = document.createElement('div');
    notice.className = 'helper-notice';
    notice.textContent = data.notice;
    box.appendChild(notice);

    const answer = document.createElement('div');
    answer.className = 'helper-answer';
    answer.textContent = data.reference_answer;
    box.appendChild(answer);

    if ((data.key_points || []).length) {
        const ul = document.createElement('ul');
        ul.className = 'helper-points';
        data.key_points.forEach(point => {
            const li = document.createElement('li');
            li.textContent = point;
            ul.appendChild(li);
        });
        box.appendChild(ul);
    }

    if (data.uncertainty) {
        const unc = document.createElement('div');
        unc.className = 'helper-uncertainty';
        unc.textContent = `条件不足之处：${data.uncertainty}`;
        box.appendChild(unc);
    }

    const meta = document.createElement('div');
    meta.className = 'helper-meta';
    meta.textContent = data.from_cache
        ? `${data.model} · 命中缓存 · 本次未产生费用`
        : `${data.model} · 本次估算花费 ${Number(data.cost || 0).toFixed(4)} 元`;
    box.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'helper-actions';
    const useBtn = document.createElement('button');
    useBtn.className = 'btn btn-primary btn-sm';
    useBtn.textContent = '用这份答案（填入标准答案框）';
    useBtn.addEventListener('click', () => {
        document.getElementById('standard-answer').value = data.reference_answer;
        showToast('已填入，请先核对再检测', 'info');
    });
    actions.appendChild(useBtn);
    box.appendChild(actions);
}

// ==================== AIGC 风格线索 ====================

function initAigcPanel() {
    const btn = document.getElementById('btn-detect-aigc');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const text = document.getElementById('aigc-text').value.trim();
        if (text.length < 20) {
            showToast('作文内容太短', 'error');
            return;
        }

        const useExternal = document.getElementById('aigc-external').checked;
        if (useExternal) {
            showToast('将调用大模型判别最可疑的几句', 'info');
        }
        await detectAigc([{ name: document.getElementById('aigc-name').value.trim(), text: text }],
                         useExternal);
    });
}

async function detectAigc(essays, useExternal) {
    showLoading(true);
    try {
        const resp = await fetchWithTimeout('/api/detect/aigc', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ essays: essays, use_external: !!useExternal })
        }, useExternal ? LLM_TIMEOUT : REQUEST_TIMEOUT);
        const data = await resp.json();
        if (data.success) {
            data.mode = 'aigc';
            state.aigcEssays = essays;      // 高亮按 essays 下标回原文
            renderResults(data);
            showToast('线索已生成，结果只用于决定先找谁聊', 'info');
        } else {
            showToast(data.error || '检测失败', 'error');
        }
    } catch (err) {
        showToast(describeFetchError(err), 'error');
    }
    showLoading(false);
}

// ==================== 学生互查 ====================

function initPairwisePanel() {
    const btn = document.getElementById('btn-detect-pairwise');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const students = collectStudents('student-cards-pair');
        if (students.length < 2) {
            showToast('互查至少需要两份有文字的答案', 'error');
            return;
        }
        if (students.some(s => !s.text)) {
            showToast('互查需要答案文字，纯照片的卡片请补充文字', 'warning');
            return;
        }

        showLoading(true);
        try {
            const resp = await fetchWithTimeout('/api/detect/pairwise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ students })
            });
            const data = await resp.json();
            if (data.success) {
                renderResults(data);
                showToast(`互查完成，共比对 ${data.pair_count} 对`, 'success');
            } else {
                showToast(data.error || '互查失败', 'error');
            }
        } catch (err) {
            showToast(describeFetchError(err), 'error');
        }
        showLoading(false);
    });
}

// ==================== 整卷多题 ====================

// 整卷状态：题目与学生答卷都由这里驱动渲染，结构变化时整体重绘
const paperState = {
    questions: [
        { id: 'Q1', stem: '', standard_answer: '', full_score: 10 },
        { id: 'Q2', stem: '', standard_answer: '', full_score: 10 },
    ],
    students: [
        { name: '', answers: {} },
        { name: '', answers: {} },
    ],
    seq: 2,
};

function initPaperPanel() {
    const addBtn = document.getElementById('btn-add-question');
    const generateBtn = document.getElementById('generate-cards-paper');
    const countInput = document.getElementById('student-count-paper');
    const decreaseBtn = document.getElementById('decrease-count-paper');
    const increaseBtn = document.getElementById('increase-count-paper');
    const detectBtn = document.getElementById('btn-detect-paper');

    if (!addBtn) return;

    addBtn.addEventListener('click', () => {
        paperState.seq += 1;
        paperState.questions.push({
            id: `Q${paperState.seq}`,
            stem: '',
            standard_answer: '',
            full_score: 10,
        });
        renderPaperQuestions();
        renderPaperStudents();
    });

    decreaseBtn.addEventListener('click', () => {
        const val = parseInt(countInput.value) || 1;
        if (val > 1) countInput.value = val - 1;
    });
    increaseBtn.addEventListener('click', () => {
        const val = parseInt(countInput.value) || 1;
        if (val < 50) countInput.value = val + 1;
    });

    generateBtn.addEventListener('click', () => {
        const count = Math.max(1, parseInt(countInput.value) || 1);
        paperState.students = Array.from({ length: count }, (_, i) => ({ name: '', answers: {} }));
        renderPaperStudents();
    });

    detectBtn.addEventListener('click', async () => {
        const questions = paperState.questions
            .map(q => ({
                id: q.id,
                stem: q.stem.trim(),
                standard_answer: q.standard_answer.trim(),
                full_score: parseFloat(q.full_score) || 10,
            }))
            .filter(q => q.standard_answer);

        if (!questions.length) {
            showToast('请至少填写一道题的标准答案', 'error');
            return;
        }
        if (!paperState.students.length) {
            showToast('请先填写人数并生成学生卡片', 'error');
            return;
        }
        const students = paperState.students.map((s, i) => ({
            name: (s.name || '').trim() || `学生${i + 1}`,
            answers: s.answers,
        }));
        const blank = students.filter(s => !Object.values(s.answers).some(v => (v || '').trim()));
        if (blank.length === students.length) {
            showToast('学生答卷不能全部为空', 'error');
            return;
        }

        showLoading(true);
        try {
            const resp = await fetchWithTimeout('/api/detect/paper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    assignment: { name: (document.getElementById('paper-name').value || '').trim(), questions },
                    students,
                })
            });
            const data = await resp.json();
            if (data.success) {
                renderResults(data);
                showToast('整卷检测完成！', 'success');
            } else {
                showToast(data.error || '整卷检测失败', 'error');
            }
        } catch (err) {
            showToast(describeFetchError(err), 'error');
        }
        showLoading(false);
    });

    renderPaperQuestions();
    renderPaperStudents();
}

function renderPaperQuestions() {
    const container = document.getElementById('paper-questions');
    if (!container) return;
    container.innerHTML = '';

    paperState.questions.forEach((question, i) => {
        const row = document.createElement('div');
        row.className = 'paper-question';
        row.dataset.qid = question.id;

        const head = document.createElement('div');
        head.className = 'paper-question-head';

        const badge = document.createElement('span');
        badge.className = 'paper-q-badge';
        badge.textContent = `第 ${i + 1} 题`;

        const scoreLabel = document.createElement('label');
        scoreLabel.className = 'paper-q-score-label';
        scoreLabel.textContent = '分值';
        const scoreInput = document.createElement('input');
        scoreInput.type = 'number';
        scoreInput.className = 'paper-q-score';
        scoreInput.min = '0.1';
        scoreInput.step = '1';
        scoreInput.value = question.full_score;
        scoreInput.addEventListener('input', () => {
            question.full_score = scoreInput.value;
        });
        scoreLabel.appendChild(scoreInput);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'paper-q-remove';
        removeBtn.type = 'button';
        removeBtn.textContent = '×';
        removeBtn.title = '删除该题';
        removeBtn.setAttribute('aria-label', '删除该题');
        removeBtn.addEventListener('click', () => {
            if (paperState.questions.length <= 1) {
                showToast('至少保留一道题', 'warning');
                return;
            }
            paperState.questions = paperState.questions.filter(q => q.id !== question.id);
            renderPaperQuestions();
            renderPaperStudents();
        });

        head.appendChild(badge);
        head.appendChild(scoreLabel);
        head.appendChild(removeBtn);

        const stemInput = document.createElement('input');
        stemInput.type = 'text';
        stemInput.className = 'form-input paper-q-stem';
        stemInput.placeholder = '题干（可留空，仅用于区分题目）';
        stemInput.value = question.stem;
        stemInput.addEventListener('input', () => {
            question.stem = stemInput.value;
        });

        const answerInput = document.createElement('textarea');
        answerInput.className = 'form-textarea paper-q-answer';
        answerInput.rows = 2;
        answerInput.placeholder = '该题的标准答案';
        answerInput.value = question.standard_answer;
        answerInput.addEventListener('input', () => {
            question.standard_answer = answerInput.value;
        });

        row.appendChild(head);
        row.appendChild(stemInput);
        row.appendChild(answerInput);
        container.appendChild(row);
    });
}

function renderPaperStudents() {
    const container = document.getElementById('student-cards-paper');
    if (!container) return;
    container.innerHTML = '';

    paperState.students.forEach((student, i) => {
        const card = document.createElement('div');
        card.className = 'student-card paper-student-card';

        const topRow = document.createElement('div');
        topRow.className = 'card-top-row';

        const idx = document.createElement('span');
        idx.className = 'card-index';
        idx.textContent = String(i + 1);

        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.className = 'student-name-input';
        nameInput.placeholder = `学生${i + 1}`;
        nameInput.value = student.name;
        nameInput.addEventListener('input', () => {
            student.name = nameInput.value;
        });

        const removeBtn = document.createElement('button');
        removeBtn.className = 'card-remove';
        removeBtn.textContent = '×';
        removeBtn.title = '删除该学生';
        removeBtn.setAttribute('aria-label', '删除该学生');
        removeBtn.addEventListener('click', () => {
            paperState.students = paperState.students.filter(s => s !== student);
            renderPaperStudents();
        });

        topRow.appendChild(idx);
        topRow.appendChild(nameInput);
        topRow.appendChild(removeBtn);
        card.appendChild(topRow);

        paperState.questions.forEach((question, qi) => {
            const answerRow = document.createElement('div');
            answerRow.className = 'paper-answer-row';

            const label = document.createElement('span');
            label.className = 'paper-answer-label';
            label.textContent = `第${qi + 1}题`;

            const input = document.createElement('textarea');
            input.className = 'form-textarea paper-answer-input';
            input.rows = 1;
            input.placeholder = '留空表示未作答（不计入整卷指数）';
            input.value = student.answers[question.id] || '';
            input.addEventListener('input', () => {
                student.answers[question.id] = input.value;
            });

            answerRow.appendChild(label);
            answerRow.appendChild(input);
            card.appendChild(answerRow);
        });

        container.appendChild(card);
    });
}

// ==================== 网络工具 ====================

/** 带超时的 fetch：外部接口挂起时不至于让界面一直转圈 */
function fetchWithTimeout(url, options = {}, timeout = REQUEST_TIMEOUT) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(timer));
}

function describeFetchError(err) {
    if (err && err.name === 'AbortError') {
        return '请求超时。本地检测不会这么慢，请检查是否在等外部接口，或改用文本检测直接粘贴答案';
    }
    return '网络错误，请重试';
}

// ==================== 结果渲染（按模式分派） ====================

function renderResults(data) {
    state.mode = data.mode || 'text';
    state.results = data.results || [];
    state.statistics = data.statistics || {};
    state.standardAnswer = data.standard_answer || '';
    state.timestamp = data.timestamp || '';
    state.pairwise = null;
    state.paper = null;
    state.aigc = [];

    document.getElementById('tab-detect').style.display = 'none';
    const panel = document.getElementById('result-panel');
    panel.style.display = 'block';

    // 先清空上一种模式留下的区块
    resetResultSections();

    if (state.mode === 'pairwise') {
        state.pairwise = {
            students: data.students || [],
            matrix: data.matrix || [],
            groups: data.groups || [],
            per_student: data.per_student || [],
            pairs: data.pairs || [],
            pair_count: data.pair_count || 0,
            pairs_truncated: !!data.pairs_truncated,
        };
        renderPairwiseResults(data);
    } else if (state.mode === 'paper') {
        state.paper = Object.assign({}, data);
        renderPaperResults(data);
    } else if (state.mode === 'aigc') {
        state.aigc = state.results;
        state.results = [];       // AIGC 不是相似度结果，别让导出按钮拿到错的形状
        renderAigcResults(data);
    } else {
        renderTextResults(data);
    }

    applyResultFilter();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function resetResultSections() {
    document.getElementById('standard-answer-display').style.display = 'none';
    document.getElementById('result-list').innerHTML = '';
    ['matrix-section', 'group-section', 'rollup-section', 'per-question-section']
        .forEach(id => { document.getElementById(id).style.display = 'none'; });
    ['matrix-grid', 'group-list', 'rollup-list', 'per-question-list']
        .forEach(id => { document.getElementById(id).innerHTML = ''; });

    // AIGC 的分档是「线索强/中/弱」，与相似度的高低风险不是一个口径，
    // 所以那套筛选片和 CSV 导出在这个模式下直接隐藏
    const isAigc = state.mode === 'aigc';
    document.getElementById('result-toolbar').style.display = isAigc ? 'none' : 'flex';
    document.querySelector('.result-actions').style.display = isAigc ? 'none' : 'flex';
}

/** 统计卡片：各模式指标口径不同，所以整块由 JS 渲染 */
function renderStats(items) {
    const grid = document.getElementById('stats-grid');
    grid.innerHTML = '';
    items.forEach(item => {
        const card = document.createElement('div');
        card.className = `stat-card stat-${item.tone || 'total'}`;
        const value = document.createElement('div');
        value.className = 'stat-value';
        value.textContent = item.value;
        const label = document.createElement('div');
        label.className = 'stat-label';
        label.textContent = item.label;
        card.appendChild(value);
        card.appendChild(label);
        grid.appendChild(card);
    });
}

function getLevelText(level) {
    const map = { high: '高风险', medium: '中风险', low: '低风险', safe: '安全' };
    return map[level] || level;
}

function percent(value, digits = 1) {
    return `${((value || 0) * 100).toFixed(digits)}%`;
}

/** 指标行：旧版历史记录只显示当年那三项，避免出现误导性的 0% */
function buildMetricLine(result) {
    const wrapper = document.createElement('div');
    wrapper.className = 'result-details';

    if (!result.metrics_version) {
        wrapper.textContent = `精确 ${percent(result.exact_match)} · 序列 ${percent(result.sequence_similarity)} · 关键词 ${percent(result.keyword_overlap)}`;
        const tag = document.createElement('span');
        tag.className = 'legacy-tag';
        tag.textContent = '旧版算法';
        wrapper.appendChild(tag);
        return wrapper;
    }

    const metrics = [
        ['精确', result.exact_match],
        ['序列', result.sequence_similarity],
        ['关键词', result.keyword_overlap],
        ['字形重合', result.char_gram_jaccard],
        ['字形分布', result.char_gram_cosine],
        ['内容覆盖', result.coverage],
    ];
    wrapper.textContent = metrics.map(([label, value]) => `${label} ${percent(value)}`).join(' · ');
    return wrapper;
}

function buildScoreBlock(score, level) {
    const scoreDiv = document.createElement('div');
    scoreDiv.className = 'result-score';
    const scoreVal = document.createElement('span');
    scoreVal.className = `score-value score-${level}`;
    scoreVal.textContent = percent(score);
    const barOuter = document.createElement('div');
    barOuter.className = 'score-bar';
    const barInner = document.createElement('div');
    barInner.className = `score-bar-fill bar-${level}`;
    barInner.style.width = `${(score * 100).toFixed(1)}%`;
    barOuter.appendChild(barInner);
    scoreDiv.appendChild(scoreVal);
    scoreDiv.appendChild(barOuter);
    return scoreDiv;
}

function buildLevelBadge(level) {
    const badge = document.createElement('span');
    badge.className = `result-badge badge-${level}`;
    badge.textContent = getLevelText(level);
    return badge;
}

// ---------- 文本 / 图片 / API 模式 ----------

function renderTextResults(data) {
    const results = data.results || [];
    const stats = data.statistics || {};

    renderStats([
        { label: '检测份数', value: stats.count || 0, tone: 'total' },
        { label: '平均相似度', value: percent(stats.avg_score), tone: 'avg' },
        { label: '高风险', value: stats.high_count || 0, tone: 'high' },
        { label: '抄袭率', value: percent(stats.plagiarism_rate), tone: 'rate' },
    ]);

    // 标准答案
    if (data.standard_answer) {
        document.getElementById('standard-answer-display').style.display = 'block';
        document.getElementById('standard-answer-text').textContent = data.standard_answer;
    }

    if (stats.low_confidence_count) {
        showToast(`${stats.low_confidence_count} 份答案过短，分数已按小样本收缩`, 'info');
    }

    const list = document.getElementById('result-list');
    results.forEach((r, i) => list.appendChild(buildTextResultItem(r, i, data)));
}

function buildTextResultItem(result, index, data) {
    const level = result.level || 'safe';
    const item = document.createElement('div');
    item.className = 'result-item';
    item.dataset.level = level;
    item.dataset.name = result.student_name || '';

    const rank = document.createElement('div');
    rank.className = `result-rank rank-${level}`;
    rank.textContent = String(index + 1);

    const info = document.createElement('div');
    info.className = 'result-info';
    const name = document.createElement('div');
    name.className = 'result-name';
    name.textContent = result.student_name;
    if (result.photos && result.photos.length > 0) {
        const photoTag = document.createElement('span');
        photoTag.className = 'result-photo-tag';
        photoTag.textContent = `📷 ${result.photos.length}张`;
        name.appendChild(photoTag);
    }
    const fragCount = (result.fragments || []).length;
    if (fragCount > 0) {
        const fragTag = document.createElement('span');
        fragTag.className = 'result-frag-tag';
        fragTag.textContent = `🔍 ${fragCount}处雷同`;
        name.appendChild(fragTag);
    }
    info.appendChild(name);
    info.appendChild(buildMetricLine(result));

    if (result.low_confidence) {
        const note = document.createElement('div');
        note.className = 'result-note';
        note.textContent = `答案过短，原始相似度 ${percent(result.raw_score)}，已按小样本收缩`;
        info.appendChild(note);
    }

    item.appendChild(rank);
    item.appendChild(info);
    item.appendChild(buildScoreBlock(result.weighted_score, level));
    item.appendChild(buildLevelBadge(level));

    // 展开区：雷同片段对照 + 学生完整答案 + 答案照片
    const expand = document.createElement('div');
    expand.className = 'result-expand';

    const fragments = result.fragments || [];
    if (fragments.length > 0 && data.standard_answer) {
        expand.appendChild(buildFragCompare({
            labelA: '标准答案',
            textA: data.standard_answer,
            labelB: result.student_name || '学生答案',
            textB: result.student_answer || '',
            fragments,
        }));
    }

    if (result.student_answer) {
        expand.appendChild(buildTextBlock('学生答案', result.student_answer));
    }

    appendPhotos(expand, result.photos);

    if (expand.children.length > 0) {
        item.appendChild(expand);
        item.classList.add('expandable');
        item.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            item.classList.toggle('expanded');
        });
    }
    return item;
}

// ---------- 学生互查模式 ----------

function renderPairwiseResults(data) {
    const stats = data.statistics || {};
    const students = data.students || [];
    const pairs = data.pairs || [];
    const groups = data.groups || [];

    renderStats([
        { label: '参与人数', value: stats.count || students.length, tone: 'total' },
        { label: '平均相似度', value: percent(stats.avg_score), tone: 'avg' },
        { label: '疑似团伙', value: stats.group_count || 0, tone: 'high' },
        { label: '最高相似对', value: percent(stats.max_score), tone: 'rate' },
    ]);

    renderMatrix(students, data.matrix || []);
    renderGroups(groups, students);

    const list = document.getElementById('result-list');
    if (data.pairs_truncated) {
        const hint = document.createElement('div');
        hint.className = 'list-hint';
        hint.textContent = `共 ${data.pair_count} 对，仅展示最相似的前 ${pairs.length} 对`;
        list.appendChild(hint);
    }
    pairs.forEach((pair, i) => list.appendChild(buildPairItem(pair, i, students)));
}

function renderMatrix(students, matrix) {
    const section = document.getElementById('matrix-section');
    const grid = document.getElementById('matrix-grid');
    if (!matrix.length || matrix.length > 60) return;

    section.style.display = 'block';
    grid.style.setProperty('--matrix-size', matrix.length + 1);

    // 表头：空白角 + 每列序号
    const corner = document.createElement('div');
    corner.className = 'matrix-cell matrix-head matrix-corner';
    corner.textContent = '#';
    grid.appendChild(corner);

    students.forEach(s => {
        const head = document.createElement('div');
        head.className = 'matrix-cell matrix-head';
        head.textContent = String(s.index + 1);
        head.title = s.name;
        grid.appendChild(head);
    });

    students.forEach((s, row) => {
        const rowHead = document.createElement('div');
        rowHead.className = 'matrix-cell matrix-head';
        rowHead.textContent = String(row + 1);
        rowHead.title = s.name;
        grid.appendChild(rowHead);

        students.forEach((other, col) => {
            const cell = document.createElement('div');
            cell.className = 'matrix-cell';
            const score = matrix[row][col] || 0;
            if (row === col) {
                cell.classList.add('matrix-self');
                cell.textContent = '—';
            } else {
                cell.textContent = Math.round(score * 100);
                cell.style.background = `rgba(99, 102, 241, ${(0.08 + 0.85 * score).toFixed(3)})`;
                cell.style.color = score >= 0.55 ? '#ffffff' : 'var(--text-primary)';
                cell.title = `${s.name} ↔ ${other.name}：${percent(score)}`;
                cell.addEventListener('click', () => focusPair(row, col));
            }
            grid.appendChild(cell);
        });
    });

    if (students.some(s => (s.name || '').length > 2)) {
        const legend = document.createElement('div');
        legend.className = 'matrix-cell matrix-names';
        legend.textContent = students.map(s => `${s.index + 1}.${s.name}`).join('   ');
        grid.appendChild(legend);
    }
}

function focusPair(row, col) {
    const card = document.querySelector(`#result-list [data-pair="${row}-${col}"]`)
        || document.querySelector(`#result-list [data-pair="${col}-${row}"]`);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 1200);
}

function renderGroups(groups, students) {
    if (!groups.length) return;
    const section = document.getElementById('group-section');
    const list = document.getElementById('group-list');

    section.style.display = 'block';
    groups.forEach((group, i) => {
        const card = document.createElement('div');
        card.className = `gang-card ${group.kind === 'gang' ? 'gang-gang' : 'gang-pair'}`;
        const level = group.min_internal >= 0.8 ? 'high'
            : (group.min_internal >= 0.5 ? 'medium' : 'low');
        card.dataset.level = level;
        card.dataset.name = group.member_names.join(' ');

        const head = document.createElement('div');
        head.className = 'gang-head';
        const title = document.createElement('span');
        title.className = 'gang-title';
        title.textContent = group.kind === 'gang'
            ? `团伙 ${i + 1}：${group.size} 人`
            : `疑似互抄：${group.size} 人`;
        head.appendChild(title);
        head.appendChild(buildLevelBadge(level));

        const members = document.createElement('div');
        members.className = 'gang-members';
        members.textContent = group.member_names.join('、');

        const metrics = document.createElement('div');
        metrics.className = 'gang-metrics';
        metrics.textContent = `最低 ${percent(group.min_internal)} · 平均 ${percent(group.avg_internal)} · 最高 ${percent(group.max_internal)}`;

        card.appendChild(head);
        card.appendChild(members);
        card.appendChild(metrics);

        if (group.evidence && group.evidence.fragment) {
            const evidence = document.createElement('div');
            evidence.className = 'gang-evidence';
            evidence.textContent = `证据（${group.evidence.a_name} ↔ ${group.evidence.b_name}）：${group.evidence.fragment}`;
            card.appendChild(evidence);
        }

        list.appendChild(card);
    });
}

function buildPairItem(pair, index, students) {
    const level = pair.level || 'safe';
    const item = document.createElement('div');
    item.className = 'result-item';
    item.dataset.level = level;
    item.dataset.name = `${pair.a_name} ${pair.b_name}`;
    item.dataset.pair = `${pair.a_index}-${pair.b_index}`;

    const rank = document.createElement('div');
    rank.className = `result-rank rank-${level}`;
    rank.textContent = String(index + 1);

    const info = document.createElement('div');
    info.className = 'result-info';
    const name = document.createElement('div');
    name.className = 'result-name';
    name.textContent = `${pair.a_name} ↔ ${pair.b_name}`;
    const fragCount = (pair.fragments || []).length;
    if (fragCount > 0) {
        const fragTag = document.createElement('span');
        fragTag.className = 'result-frag-tag';
        fragTag.textContent = `🔍 ${fragCount}处雷同`;
        name.appendChild(fragTag);
    }
    info.appendChild(name);
    info.appendChild(buildMetricLine({
        metrics_version: pair.metrics_version,
        ...(pair.metrics || {}),
    }));

    item.appendChild(rank);
    item.appendChild(info);
    item.appendChild(buildScoreBlock(pair.score, level));
    item.appendChild(buildLevelBadge(level));

    // 展开：两名学生的答案左右对照并高亮雷同处
    const expand = document.createElement('div');
    expand.className = 'result-expand';

    const studentA = students.find(s => s.index === pair.a_index);
    const studentB = students.find(s => s.index === pair.b_index);
    const textA = studentA ? studentA.text : '';
    const textB = studentB ? studentB.text : '';

    if ((pair.fragments || []).length > 0 && textA && textB) {
        expand.appendChild(buildFragCompare({
            labelA: pair.a_name,
            textA,
            labelB: pair.b_name,
            textB,
            fragments: pair.fragments,
        }));
    } else if (textA || textB) {
        expand.appendChild(buildTextBlock(pair.a_name, textA));
        expand.appendChild(buildTextBlock(pair.b_name, textB));
    }

    appendPhotos(expand, studentA && studentA.photos, `${pair.a_name} 的答案照片`);
    appendPhotos(expand, studentB && studentB.photos, `${pair.b_name} 的答案照片`);

    if (expand.children.length > 0) {
        item.appendChild(expand);
        item.classList.add('expandable');
        item.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            item.classList.toggle('expanded');
        });
    }
    return item;
}

// ---------- 整卷多题模式 ----------

function renderPaperResults(data) {
    const stats = data.statistics || {};
    const rollup = data.rollup || [];
    const perQuestion = data.per_question || [];

    // 雷同占分最高的学生，比整卷平均分更能说明问题
    const worst = rollup.reduce((acc, r) => (r.high_risk_score > (acc ? acc.high_risk_score : -1) ? r : acc), null);

    renderStats([
        { label: '学生人数', value: stats.count || rollup.length, tone: 'total' },
        { label: '整卷平均', value: percent(stats.avg_score), tone: 'avg' },
        {
            label: '雷同占分最高',
            value: worst && worst.high_risk_score > 0 ? `${worst.high_risk_score}分` : '—',
            tone: 'high',
        },
        { label: '抄袭率', value: percent(stats.plagiarism_rate), tone: 'rate' },
    ]);

    if (stats.weakest_question) {
        showToast(`全班最可疑的是 ${stats.weakest_question}`, 'warning');
    }

    renderRollup(rollup, data);
    renderPerQuestion(perQuestion);
}

function renderRollup(rollup, data) {
    const section = document.getElementById('rollup-section');
    const list = document.getElementById('rollup-list');
    if (!rollup.length) return;
    section.style.display = 'block';

    rollup.forEach((row, i) => {
        const item = document.createElement('div');
        item.className = 'result-item rollup-item';
        item.dataset.level = row.level;
        item.dataset.name = row.name;

        const rank = document.createElement('div');
        rank.className = `result-rank rank-${row.level}`;
        rank.textContent = String(i + 1);

        const info = document.createElement('div');
        info.className = 'result-info';
        const name = document.createElement('div');
        name.className = 'result-name';
        name.textContent = row.name;
        if (row.missing_count) {
            const tag = document.createElement('span');
            tag.className = 'result-photo-tag';
            tag.textContent = `${row.missing_count} 题未答`;
            name.appendChild(tag);
        }
        info.appendChild(name);

        const summary = document.createElement('div');
        summary.className = 'result-details';
        if (row.high_risk_questions && row.high_risk_questions.length) {
            summary.textContent = `高度雷同 ${row.high_risk_questions.length} 题（${row.high_risk_questions.join('、')}），占 ${row.high_risk_score}/${row.answered_full_score} 分`;
        } else {
            summary.textContent = `无高度雷同题目 · 已答 ${row.answered_count} 题，计 ${row.answered_full_score} 分`;
        }
        info.appendChild(summary);

        if (row.weakest_question) {
            const weakest = document.createElement('div');
            weakest.className = 'result-note';
            weakest.textContent = `最可疑：${row.weakest_question}（${percent(row.weakest_score)}）`;
            info.appendChild(weakest);
        }

        item.appendChild(rank);
        item.appendChild(info);
        item.appendChild(buildScoreBlock(row.paper_score, row.level));
        item.appendChild(buildLevelBadge(row.level));

        const expand = document.createElement('div');
        expand.className = 'result-expand';
        expand.appendChild(buildPaperBreakdown(row, data));
        appendPhotos(expand, row.photos);

        item.appendChild(expand);
        item.classList.add('expandable');
        item.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            item.classList.toggle('expanded');
        });
        list.appendChild(item);
    });
}

function buildPaperBreakdown(row, data) {
    const wrapper = document.createElement('div');
    wrapper.className = 'paper-breakdown';

    const questions = data.questions || [];
    questions.forEach(question => {
        const score = row.per_question[question.id];
        const line = document.createElement('div');
        line.className = 'paper-breakdown-row';

        const label = document.createElement('span');
        label.className = 'paper-breakdown-label';
        label.textContent = question.id;

        const value = document.createElement('span');
        if (score === undefined) {
            value.className = 'paper-breakdown-score is-missing';
            value.textContent = '未作答';
        } else {
            const level = score >= 0.8 ? 'high' : (score >= 0.5 ? 'medium' : (score >= 0.3 ? 'low' : 'safe'));
            value.className = `paper-breakdown-score score-${level}`;
            value.textContent = percent(score);
        }

        line.appendChild(label);
        line.appendChild(value);
        wrapper.appendChild(line);
    });
    return wrapper;
}

function renderPerQuestion(perQuestion) {
    if (!perQuestion.length) return;
    const section = document.getElementById('per-question-section');
    const list = document.getElementById('per-question-list');
    section.style.display = 'block';

    perQuestion.forEach(question => {
        const card = document.createElement('div');
        card.className = 'question-card';

        const head = document.createElement('button');
        head.type = 'button';
        head.className = 'question-head';
        head.innerHTML = '';

        const title = document.createElement('span');
        title.className = 'question-title';
        title.textContent = `${question.qid}（${question.full_score}分）`;
        const meta = document.createElement('span');
        meta.className = 'question-meta';
        meta.textContent = `已答 ${question.answered_count} 人 · 平均 ${percent((question.statistics || {}).avg_score)}`;
        head.appendChild(title);
        head.appendChild(meta);

        const body = document.createElement('div');
        body.className = 'question-body';
        if (question.standard_answer) {
            body.appendChild(buildTextBlock('标准答案', question.standard_answer));
        }
        (question.results || []).forEach(result => {
            body.appendChild(buildQuestionResultRow(result, question));
        });

        head.addEventListener('click', () => card.classList.toggle('expanded'));
        card.appendChild(head);
        card.appendChild(body);
        list.appendChild(card);
    });
}

function buildQuestionResultRow(result, question) {
    const row = document.createElement('div');
    row.className = 'question-result';

    const head = document.createElement('div');
    head.className = 'question-result-head';
    const name = document.createElement('span');
    name.className = 'question-result-name';
    name.textContent = result.student_name;
    const score = document.createElement('span');
    score.className = `question-result-score score-${result.level}`;
    score.textContent = percent(result.weighted_score);
    head.appendChild(name);
    head.appendChild(score);
    row.appendChild(head);

    if (result.student_answer) {
        row.appendChild(buildTextBlock('学生答案', result.student_answer));
    }
    if ((result.fragments || []).length > 0 && question.standard_answer) {
        row.appendChild(buildFragCompare({
            labelA: '标准答案',
            textA: question.standard_answer,
            labelB: result.student_name,
            textB: result.student_answer || '',
            fragments: result.fragments,
        }));
    }
    return row;
}

// ---------- 片段对照与公共组件 ----------

function buildTextBlock(label, text) {
    const wrap = document.createElement('div');
    const labelEl = document.createElement('div');
    labelEl.className = 'expand-label';
    labelEl.textContent = label;
    const body = document.createElement('div');
    body.className = 'expand-text';
    body.textContent = text;
    wrap.appendChild(labelEl);
    wrap.appendChild(body);
    return wrap;
}

/**
 * 雷同片段左右对照：按片段区间把原文切开、命中处用 <mark> 包住。
 * 全程用 textContent 组装，绝不把答案文本拼进 innerHTML。
 */
function buildFragCompare({ labelA, textA, labelB, textB, fragments }) {
    const wrap = document.createElement('div');
    wrap.className = 'frag-compare';

    const rangesA = normalizeRanges(fragments.map(f => ({ start: f.a_start, end: f.a_end })), textA.length);
    const rangesB = normalizeRanges(fragments.map(f => ({ start: f.b_start, end: f.b_end })), textB.length);

    wrap.appendChild(buildFragColumn('frag-a', labelA, textA, rangesA));
    wrap.appendChild(buildFragColumn('frag-b', labelB, textB, rangesB));

    const summary = document.createElement('div');
    summary.className = 'frag-summary';
    summary.textContent = `共 ${fragments.length} 处雷同，最长 ${Math.max(...fragments.map(f => f.len || 0))} 字`;
    wrap.appendChild(summary);

    return wrap;
}

function buildFragColumn(className, label, text, ranges) {
    const col = document.createElement('div');
    col.className = `frag-col ${className}`;
    const labelEl = document.createElement('div');
    labelEl.className = 'frag-col-label';
    labelEl.textContent = label;
    const body = document.createElement('div');
    body.className = 'frag-col-text';
    body.appendChild(highlightRanges(text, ranges));
    col.appendChild(labelEl);
    col.appendChild(body);
    return col;
}

function normalizeRanges(ranges, maxLength) {
    const cleaned = ranges
        .filter(r => Number.isFinite(r.start) && Number.isFinite(r.end) && r.end > r.start)
        .map(r => ({ start: Math.max(0, r.start), end: Math.min(maxLength, r.end) }))
        .sort((a, b) => a.start - b.start);

    const merged = [];
    cleaned.forEach(r => {
        const last = merged[merged.length - 1];
        if (last && r.start <= last.end) {
            last.end = Math.max(last.end, r.end);
        } else {
            merged.push({ ...r });
        }
    });
    return merged;
}

function highlightRanges(text, ranges) {
    const frag = document.createDocumentFragment();
    let cursor = 0;
    ranges.forEach(range => {
        if (range.start > cursor) {
            frag.appendChild(document.createTextNode(text.slice(cursor, range.start)));
        }
        const mark = document.createElement('mark');
        mark.className = 'frag-hit';
        mark.textContent = text.slice(range.start, range.end);
        frag.appendChild(mark);
        cursor = range.end;
    });
    if (cursor < text.length) {
        frag.appendChild(document.createTextNode(text.slice(cursor)));
    }
    return frag;
}

function appendPhotos(container, photos, label) {
    if (!photos || !photos.length) return;
    const labelEl = document.createElement('div');
    labelEl.className = 'expand-label';
    labelEl.textContent = label || `答案照片（${photos.length} 张，点击放大）`;
    const grid = document.createElement('div');
    grid.className = 'expand-photos';
    photos.forEach(url => {
        const a = document.createElement('a');
        a.href = url;
        a.target = '_blank';
        a.className = 'expand-photo';
        const img = document.createElement('img');
        img.src = url;
        img.alt = '答案照片';
        img.loading = 'lazy';
        a.appendChild(img);
        grid.appendChild(a);
    });
    container.appendChild(labelEl);
    container.appendChild(grid);
}

// ---------- AIGC 风格线索 ----------

function renderAigcResults(data) {
    const results = data.results || [];
    const stats = data.statistics || {};

    renderStats([
        { label: '检测篇数', value: stats.count || 0, tone: 'total' },
        { label: '线索偏强', value: stats.strong_count || 0, tone: 'high' },
        { label: '线索中等', value: stats.medium_count || 0, tone: 'avg' },
        { label: '本次接口花费', value: `${Number(stats.estimated_cost || 0).toFixed(3)} 元`, tone: 'rate' },
    ]);

    if (stats.rejected) {
        showToast(`${stats.rejected} 篇太短，未出线索（作文至少 60 字）`, 'info');
    }

    const list = document.getElementById('result-list');
    results.forEach((item, i) => list.appendChild(buildAigcCard(item, i)));
}

function buildAigcCard(item, index) {
    const card = document.createElement('div');
    card.className = 'aigc-card';
    card.dataset.level = item.level || 'weak';

    const head = document.createElement('div');
    head.className = 'aigc-head';
    const title = document.createElement('h3');
    title.className = 'aigc-title';
    title.textContent = item.name || `作文${index + 1}`;
    head.appendChild(title);

    if (!item.success) {
        const error = document.createElement('div');
        error.className = 'aigc-error';
        error.textContent = item.error || '未能出线索';
        card.appendChild(head);
        card.appendChild(error);
        if (item.hint) {
            const hint = document.createElement('div');
            hint.className = 'aigc-hint';
            hint.textContent = item.hint;
            card.appendChild(hint);
        }
        return card;
    }

    const badge = document.createElement('span');
    badge.className = `aigc-badge level-${item.level}`;
    badge.textContent = item.level_label;
    head.appendChild(badge);

    const score = document.createElement('div');
    score.className = 'aigc-score';
    score.textContent = percent(item.ai_score, 1);
    head.appendChild(score);
    card.appendChild(head);

    const meter = document.createElement('div');
    meter.className = 'aigc-meter';
    const fill = document.createElement('div');
    fill.className = `aigc-meter-fill fill-${item.level}`;
    fill.style.width = `${(item.ai_score * 100).toFixed(1)}%`;
    meter.appendChild(fill);
    card.appendChild(meter);

    const meta = document.createElement('div');
    meta.className = 'aigc-meta';
    const stat = item.statistics || {};
    const bits = [
        `${item.char_count} 字`,
        `${stat.sentences || 0} 句`,
        `${stat.paragraphs || 0} 段`,
        `置信度 ${percent(item.confidence, 0)}`,
    ];
    if (item.low_confidence) bits.push('样本偏短');
    meta.textContent = bits.join(' · ');
    card.appendChild(meta);

    card.appendChild(buildSignalList(item.signals || []));
    card.appendChild(buildAigcEvidence(item));

    const external = buildExternalBlock(item);
    if (external) card.appendChild(external);

    const notice = document.createElement('div');
    notice.className = 'aigc-notice';
    notice.textContent = item.notice;
    card.appendChild(notice);

    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn btn-outline btn-sm aigc-copy';
    copyBtn.textContent = '复制线索摘要（用于约谈记录）';
    copyBtn.addEventListener('click', () => {
        copyToClipboard(buildAigcSummary(item), '线索摘要已复制');
    });
    card.appendChild(copyBtn);

    return card;
}

function buildSignalList(signals) {
    const wrap = document.createElement('div');
    wrap.className = 'aigc-signals';

    const title = document.createElement('div');
    title.className = 'expand-label';
    title.textContent = '各项信号（数值越高越像机器腔）';
    wrap.appendChild(title);

    signals.forEach(signal => {
        const row = document.createElement('div');
        row.className = 'aigc-signal' + (signal.applicable ? '' : ' is-na');

        const label = document.createElement('span');
        label.className = 'aigc-signal-label';
        label.textContent = signal.label;
        row.appendChild(label);

        const bar = document.createElement('span');
        bar.className = 'aigc-signal-bar';
        if (signal.applicable) {
            const inner = document.createElement('i');
            inner.style.width = `${(signal.tendency * 100).toFixed(0)}%`;
            inner.className = signal.tendency >= 0.66 ? 'is-high'
                : signal.tendency >= 0.34 ? 'is-mid' : 'is-low';
            bar.appendChild(inner);
        }
        row.appendChild(bar);

        const value = document.createElement('span');
        value.className = 'aigc-signal-value';
        value.textContent = signal.applicable ? percent(signal.tendency, 0) : '样本不足';
        row.appendChild(value);

        const metric = document.createElement('span');
        metric.className = 'aigc-signal-metric';
        metric.textContent = signal.metric || signal.reading;
        row.appendChild(metric);

        wrap.appendChild(row);

        const reading = document.createElement('div');
        reading.className = 'aigc-signal-reading';
        reading.textContent = signal.reading;
        wrap.appendChild(reading);
    });

    return wrap;
}

/** 证据块：把整篇作文重画一遍，可疑句子用 mark 圈起来，下面列出圈出的理由 */
function buildAigcEvidence(item) {
    const wrap = document.createElement('div');
    wrap.className = 'aigc-evidence';

    const title = document.createElement('div');
    title.className = 'expand-label';
    title.textContent = '最可疑的句子';
    wrap.appendChild(title);

    const source = (state.aigcEssays[item.index] || {}).text || '';
    const ranges = normalizeRanges(
        (item.evidence || []).map(one => ({ start: one.start, end: one.end })),
        source.length);
    if (source && ranges.length) {
        const body = document.createElement('div');
        body.className = 'aigc-text';
        body.appendChild(highlightRanges(source, ranges));
        wrap.appendChild(body);
    }

    (item.evidence || []).forEach(one => {
        const row = document.createElement('div');
        row.className = 'aigc-ev-row';

        const head = document.createElement('div');
        head.className = 'aigc-ev-head';
        const tag = document.createElement('span');
        tag.className = 'aigc-ev-score';
        tag.textContent = `可疑度 ${percent(one.score, 0)}`;
        head.appendChild(tag);
        const text = document.createElement('span');
        text.className = 'aigc-ev-text';
        text.textContent = one.text;
        head.appendChild(text);
        row.appendChild(head);

        const reasons = document.createElement('div');
        reasons.className = 'aigc-ev-reasons';
        reasons.textContent = (one.reasons || []).join('；');
        row.appendChild(reasons);

        wrap.appendChild(row);
    });

    if (!(item.evidence || []).length) {
        const empty = document.createElement('div');
        empty.className = 'aigc-ev-reasons';
        empty.textContent = '没有明显可疑的句子。';
        wrap.appendChild(empty);
    }
    return wrap;
}

function buildExternalBlock(item) {
    const external = item.external;
    if (!external) return null;

    const wrap = document.createElement('div');
    wrap.className = 'aigc-external' + (external.success ? '' : ' is-failed');
    const title = document.createElement('div');
    title.className = 'expand-label';
    title.textContent = '大模型二次判别';
    wrap.appendChild(title);

    if (!external.success) {
        const error = document.createElement('div');
        error.className = 'aigc-error';
        error.textContent = external.error || '外部判别未完成';
        wrap.appendChild(error);
        if (external.hint) {
            const hint = document.createElement('div');
            hint.className = 'aigc-hint';
            hint.textContent = external.hint;
            wrap.appendChild(hint);
        }
        return wrap;
    }

    const line = document.createElement('div');
    line.className = 'aigc-ext-line';
    line.textContent = [
        `接口判分 ${percent(external.score, 1)}`,
        `本地 ${percent(item.local_score, 1)}`,
        `融合后 ${percent(item.ai_score, 1)}（外部占 ${percent(external.fused_with, 0)}）`,
        `送检 ${external.sentences_sent} 句`,
        external.from_cache ? '命中缓存，本次未产生费用'
            : `本次估算 ${Number(external.cost || 0).toFixed(4)} 元`,
    ].join(' · ');
    wrap.appendChild(line);

    if ((external.reasons || []).length) {
        const ul = document.createElement('ul');
        ul.className = 'aigc-ext-reasons';
        external.reasons.forEach(reason => {
            const li = document.createElement('li');
            li.textContent = reason;
            ul.appendChild(li);
        });
        wrap.appendChild(ul);
    }

    if (external.uncertainty) {
        const unc = document.createElement('div');
        unc.className = 'aigc-hint';
        unc.textContent = `接口自己承认的局限：${external.uncertainty}`;
        wrap.appendChild(unc);
    }
    return wrap;
}

function buildAigcSummary(item) {
    const lines = [
        `标注：${item.name || '未命名'}`,
        `线索强度：${item.level_label}（${percent(item.ai_score, 1)}）`,
        `全文 ${item.char_count} 字 / ${(item.statistics || {}).sentences || 0} 句`,
        '',
        '各项信号：',
    ];
    (item.signals || []).forEach(signal => {
        lines.push(signal.applicable
            ? `- ${signal.label}：${signal.metric} → ${signal.reading}`
            : `- ${signal.label}：样本不足`);
    });
    lines.push('', '可疑句子：');
    (item.evidence || []).forEach(one => {
        lines.push(`- [${percent(one.score, 0)}] ${one.text}`);
        if ((one.reasons || []).length) lines.push(`    原因：${one.reasons.join('；')}`);
    });
    lines.push('', item.notice);
    return lines.join('\n');
}

// ==================== 结果筛选 ====================

function initResultToolbar() {
    const chips = document.getElementById('filter-chips');
    if (!chips) return;

    chips.addEventListener('click', (e) => {
        const chip = e.target.closest('.filter-chip');
        if (!chip) return;
        chips.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        applyResultFilter();
    });

    document.getElementById('result-search').addEventListener('input', applyResultFilter);
}

/** 按风险等级与姓名过滤：矩阵保留全量，其余带 data-level 的卡片参与过滤 */
function applyResultFilter() {
    const active = document.querySelector('.filter-chip.active');
    const level = active ? active.dataset.level : 'all';
    const keyword = (document.getElementById('result-search').value || '').trim().toLowerCase();

    document.querySelectorAll('#result-panel [data-level]').forEach(el => {
        const levelOk = level === 'all' || el.dataset.level === level;
        const nameOk = !keyword || (el.dataset.name || '').toLowerCase().includes(keyword);
        el.classList.toggle('filtered-out', !(levelOk && nameOk));
    });
}

// ==================== 操作按钮 ====================

function initActions() {
    document.getElementById('btn-export').addEventListener('click', () => exportReport('txt'));
    document.getElementById('btn-export-csv').addEventListener('click', () => exportReport('csv'));

    document.getElementById('btn-reset').addEventListener('click', () => {
        document.getElementById('result-panel').style.display = 'none';
        document.getElementById('tab-detect').style.display = 'block';
        resetResultSections();
        state.pairwise = null;
        state.paper = null;
    });
}

/** 各模式的导出载荷：统一带上 mode，后端据此生成对应报告 */
function buildExportPayload(format) {
    const payload = {
        format,
        mode: state.mode,
        statistics: state.statistics,
        standard_answer: state.standardAnswer,
        timestamp: state.timestamp,
        results: [],
    };

    if (state.mode === 'pairwise' && state.pairwise) {
        payload.results = state.pairwise.pairs.map(p => ({
            student_name: `${p.a_name} ↔ ${p.b_name}`,
            weighted_score: p.score,
            level: p.level,
            metrics_version: p.metrics_version,
            ...(p.metrics || {}),
            fragments: p.fragments || [],
        }));
    } else if (state.mode === 'paper' && state.paper) {
        payload.results = (state.paper.rollup || []).map(row => ({
            student_name: row.name,
            weighted_score: row.paper_score,
            level: row.level,
            student_answer: `高度雷同 ${(row.high_risk_questions || []).length} 题，`
                + `占 ${row.high_risk_score}/${row.answered_full_score} 分`,
            ...(row.avg_metrics || {}),
        }));
    } else {
        payload.results = state.results || [];
    }
    return payload;
}

async function exportReport(format) {
    const payload = buildExportPayload(format);
    if (!payload.results.length) {
        showToast('没有可导出的数据', 'warning');
        return;
    }

    try {
        const resp = await fetchWithTimeout('/api/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }, 30000);
        if (!resp.ok) {
            showToast('导出失败', 'error');
            return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = format === 'csv' ? '抄袭检测报告.csv' : '抄袭检测报告.txt';
        a.click();
        URL.revokeObjectURL(url);
        showToast('报告已导出', 'success');
    } catch (err) {
        showToast('导出失败', 'error');
    }
}

// ==================== 设置面板 ====================

const WEIGHT_SLIDERS = [
    ['weight-exact', 'exact_match'],
    ['weight-sequence', 'sequence_similarity'],
    ['weight-keyword', 'keyword_overlap'],
    ['weight-gram-jaccard', 'char_gram_jaccard'],
    ['weight-gram-cosine', 'char_gram_cosine'],
    ['weight-coverage', 'coverage'],
];

function initSettings() {
    const sliders = [
        { id: 'weight-exact', display: 'weight-exact-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-sequence', display: 'weight-sequence-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-keyword', display: 'weight-keyword-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-gram-jaccard', display: 'weight-gram-jaccard-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-gram-cosine', display: 'weight-gram-cosine-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-coverage', display: 'weight-coverage-val', format: v => (v / 100).toFixed(2) },
        { id: 'threshold-high', display: 'threshold-high-val', format: v => v + '%' },
        { id: 'threshold-medium', display: 'threshold-medium-val', format: v => v + '%' },
        { id: 'calibration-low-len', display: 'calibration-low-len-val', format: v => v + '字' },
        { id: 'fragment-min-len', display: 'fragment-min-len-val', format: v => v + '字' },
        { id: 'cluster-margin', display: 'cluster-margin-val', format: v => (v / 100).toFixed(2) },
    ];

    sliders.forEach(({ id, display, format }) => {
        const slider = document.getElementById(id);
        const val = document.getElementById(display);
        if (slider && val) {
            slider.addEventListener('input', () => {
                val.textContent = format(parseInt(slider.value));
                markProfileCustom();
            });
        }
    });

    // AIGC 滑块单独绑：动它不该把相似度档位改成「自定义」
    [
        { id: 'aigc-strong', display: 'aigc-strong-val', format: v => v + '%' },
        { id: 'aigc-medium', display: 'aigc-medium-val', format: v => v + '%' },
        { id: 'aigc-min-chars', display: 'aigc-min-chars-val', format: v => v + '字' },
        { id: 'aigc-external-weight', display: 'aigc-external-weight-val', format: v => v + '%' },
        { id: 'aigc-evidence-max', display: 'aigc-evidence-max-val', format: v => v + '句' },
    ].forEach(({ id, display, format }) => {
        const slider = document.getElementById(id);
        const val = document.getElementById(display);
        if (slider && val) {
            slider.addEventListener('input', () => {
                val.textContent = format(parseInt(slider.value));
            });
        }
    });

    initLlmActions();

    // 选择档位即套用该档位的权重
    document.getElementById('preset-select').addEventListener('change', async (e) => {
        const profile = e.target.value;
        if (profile === 'custom') return;
        await saveSettings({ profile });
    });

    loadSettings();

    document.getElementById('btn-save-settings').addEventListener('click', async () => {
        await saveSettings(collectSettingsPayload());
    });
}

function initLlmActions() {
    const testBtn = document.getElementById('btn-llm-test');
    if (testBtn) {
        testBtn.addEventListener('click', async () => {
            const status = document.getElementById('llm-status');
            testBtn.disabled = true;
            status.textContent = '正在测试接口…（会产生一次极小的调用）';
            try {
                const resp = await fetchWithTimeout('/api/llm/test', { method: 'POST' }, LLM_TIMEOUT);
                const data = await resp.json();
                if (data.success) {
                    status.textContent = `${data.message} · 返回「${data.reply}」· 本次 ${Number(data.cost).toFixed(4)} 元`;
                    renderLlmStatus(data.llm);
                    showToast('接口可用', 'success');
                } else {
                    status.textContent = `${data.error}${data.hint ? '｜' + data.hint : ''}`;
                    showToast('接口测试未通过', 'error');
                }
            } catch (err) {
                status.textContent = describeFetchError(err);
            }
            testBtn.disabled = false;
        });
    }

    const cacheBtn = document.getElementById('btn-llm-cache');
    if (cacheBtn) {
        cacheBtn.addEventListener('click', () => {
            saveSettings({ clear_cache: true });
        });
    }
}

/** 用量与预算：教师随时知道自己花了多少 */
function renderLlmStatus(llm) {
    const status = document.getElementById('llm-status');
    if (!status || !llm) return;

    const today = llm.today || {};
    const parts = [];
    parts.push(llm.has_key ? `密钥 ${llm.key_masked}` : '未填写密钥');
    parts.push(llm.enabled ? '开关已开' : '开关已关');
    parts.push(`今日 ${today.calls || 0} 次调用 / 估算 ${(today.cost || 0).toFixed(4)} 元`);
    if (llm.daily_budget > 0) {
        parts.push(`预算 ${llm.daily_budget} 元，剩余 ${Number(llm.budget_remaining || 0).toFixed(4)} 元`);
    } else {
        parts.push('未设预算上限');
    }
    parts.push(`缓存 ${((llm.cache || {}).entries || 0)} 条`);
    if (!llm.readiness.ok) parts.push(`注意：${llm.readiness.message}`);
    status.textContent = parts.join(' · ');
}

function markProfileCustom() {
    const select = document.getElementById('preset-select');
    if (select) select.value = 'custom';
}

function collectSettingsPayload() {
    const weights = {};
    WEIGHT_SLIDERS.forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) weights[key] = parseInt(el.value) / 100;
    });

    const payload = {
        weights,
        thresholds: {
            high: parseInt(document.getElementById('threshold-high').value) / 100,
            medium: parseInt(document.getElementById('threshold-medium').value) / 100,
        },
        calibration: {
            enabled: document.getElementById('calibration-enabled').checked,
            low_confidence_len: parseInt(document.getElementById('calibration-low-len').value),
        },
        fragments: {
            min_len: parseInt(document.getElementById('fragment-min-len').value),
        },
        normalize: {
            traditional: document.getElementById('normalize-traditional').checked,
        },
        cluster_margin: parseInt(document.getElementById('cluster-margin').value) / 100,
        aigc: {
            levels: {
                strong: parseInt(document.getElementById('aigc-strong').value) / 100,
                medium: parseInt(document.getElementById('aigc-medium').value) / 100,
            },
            min_chars: parseInt(document.getElementById('aigc-min-chars').value),
            external_weight: parseInt(document.getElementById('aigc-external-weight').value) / 100,
            evidence_max: parseInt(document.getElementById('aigc-evidence-max').value),
        },
        llm: {
            enabled: document.getElementById('llm-enabled').checked,
            base_url: document.getElementById('llm-base-url').value.trim(),
            model: document.getElementById('llm-model').value.trim(),
            daily_budget: parseFloat(document.getElementById('llm-budget').value) || 0,
            prices: {
                input: parseFloat(document.getElementById('llm-price-in').value) || 0,
                output: parseFloat(document.getElementById('llm-price-out').value) || 0,
            },
        },
    };

    // 密钥框留空表示「不改」，不能把空串当「清除」提交
    const keyInput = document.getElementById('llm-key');
    const keyValue = keyInput ? keyInput.value.trim() : '';
    if (keyValue) {
        payload.api_key = keyValue;
        keyInput.value = '';
    }
    return payload;
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/settings');
        const data = await resp.json();
        if (!data.success) return;
        applySettingsToForm(data);
    } catch (err) {
        // 设置读取失败不影响检测，静默处理
    }
}

function applySettingsToForm(data) {
    const select = document.getElementById('preset-select');
    if (select && data.profile) select.value = data.profile;

    const weights = data.weights || {};
    const setWeight = (id, value) => setSlider(id, Math.round((value || 0) * 100));
    setWeight('weight-exact', weights.exact_match);
    setWeight('weight-sequence', weights.sequence_similarity);
    setWeight('weight-keyword', weights.keyword_overlap);
    setWeight('weight-gram-jaccard', weights.char_gram_jaccard);
    setWeight('weight-gram-cosine', weights.char_gram_cosine);
    setWeight('weight-coverage', weights.coverage);

    const thresholds = data.thresholds || {};
    setSlider('threshold-high', Math.round((thresholds.high || 0.8) * 100));
    setSlider('threshold-medium', Math.round((thresholds.medium || 0.5) * 100));

    const calibration = data.calibration || {};
    document.getElementById('calibration-enabled').checked = calibration.enabled !== false;
    setSlider('calibration-low-len', calibration.low_confidence_len || 12);

    const fragments = data.fragments || {};
    setSlider('fragment-min-len', fragments.min_len || 8);

    const normalize = data.normalize || {};
    document.getElementById('normalize-traditional').checked = !!normalize.traditional;

    setSlider('cluster-margin', Math.round((data.cluster_margin || 0.15) * 100));

    const aigc = data.aigc || {};
    const levels = aigc.levels || {};
    setSlider('aigc-strong', Math.round((levels.strong || 0.6) * 100));
    setSlider('aigc-medium', Math.round((levels.medium || 0.4) * 100));
    setSlider('aigc-min-chars', aigc.min_chars || 60);
    setSlider('aigc-external-weight', Math.round((aigc.external_weight || 0) * 100));
    setSlider('aigc-evidence-max', aigc.evidence_max || 6);

    applyLlmToForm(data.llm);
}

function applyLlmToForm(llm) {
    if (!llm) return;
    document.getElementById('llm-enabled').checked = !!llm.enabled;
    document.getElementById('llm-base-url').value = llm.base_url || '';
    document.getElementById('llm-model').value = llm.model || '';
    document.getElementById('llm-budget').value = llm.daily_budget;
    const prices = llm.prices || {};
    document.getElementById('llm-price-in').value = prices.input;
    document.getElementById('llm-price-out').value = prices.output;
    // 密钥字段永远不从服务端回填，只保留「留空即不修改」的提示
    const placeholder = llm.has_key ? `已保存 ${llm.key_masked}（留空即不修改）` : '尚未填写';
    document.getElementById('llm-key').placeholder = placeholder;
    renderLlmStatus(llm);
}

async function saveSettings(payload) {
    try {
        const resp = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.success) {
            showToast(data.cache_cleared ? `已清空 ${data.cache_cleared} 条接口缓存` : '设置已保存并生效',
                      'success');
            await loadSettings();
        } else {
            showToast(data.error || '保存失败', 'error');
        }
    } catch (err) {
        showToast('保存失败', 'error');
    }
}

function setSlider(id, value) {
    const slider = document.getElementById(id);
    const display = document.getElementById(id + '-val');
    if (!slider) return;
    slider.value = value;
    if (!display) return;
    if (id.startsWith('weight') || id === 'cluster-margin') {
        display.textContent = (value / 100).toFixed(2);
    } else if (id === 'threshold-high' || id === 'threshold-medium'
        || id === 'aigc-strong' || id === 'aigc-medium' || id === 'aigc-external-weight') {
        display.textContent = value + '%';
    } else if (id === 'aigc-evidence-max') {
        display.textContent = value + '句';
    } else {
        display.textContent = value + '字';
    }
}

// ==================== 工具函数 ====================

function showLoading(show) {
    document.getElementById('loading-overlay').style.display = show ? 'flex' : 'none';
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    const iconSpan = document.createElement('span');
    iconSpan.textContent = icons[type] || '';
    const msgSpan = document.createElement('span');
    msgSpan.textContent = message;
    toast.appendChild(iconSpan);
    toast.appendChild(msgSpan);

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        setTimeout(() => toast.remove(), 300);
    }, type === 'error' ? 6000 : 3200);
}

/**
 * 复制到剪贴板。
 *
 * navigator.clipboard 在非安全上下文（用局域网 IP 打开又不是 https）不可用，
 * 所以回退到临时 textarea + execCommand，手机浏览器也能复制出去。
 */
function copyToClipboard(text, okMessage) {
    const done = () => showToast(okMessage || '已复制', 'success');

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
        return;
    }
    fallbackCopy(text, done);
}

function fallbackCopy(text, done) {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.className = 'clipboard-shadow';
    document.body.appendChild(area);
    area.select();

    let copied = false;
    try {
        copied = document.execCommand('copy');
    } catch (err) {
        copied = false;
    }
    area.remove();

    if (copied) done();
    else showToast('这个浏览器不让网页复制，请长按结果手动选中', 'warning');
}

// ==================== 批量导入 ====================

function initBatchImport() {
    const modal = document.getElementById('batch-modal');
    const openBtn = document.getElementById('btn-batch-import');
    const closeBtn = document.getElementById('batch-modal-close');
    const cancelBtn = document.getElementById('batch-cancel');
    const confirmBtn = document.getElementById('batch-confirm');

    if (!openBtn) return;

    openBtn.addEventListener('click', () => {
        modal.style.display = 'flex';
    });

    const closeModal = () => { modal.style.display = 'none'; };
    closeBtn.addEventListener('click', closeModal);
    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    confirmBtn.addEventListener('click', () => {
        const text = document.getElementById('batch-text').value.trim();
        if (!text) {
            showToast('请输入内容', 'warning');
            return;
        }

        const lines = text.split('\n').filter(l => l.trim());
        const container = document.getElementById('student-cards');
        container.innerHTML = '';

        lines.forEach((line, i) => {
            const parts = line.split(',');
            let name, content;
            if (parts.length >= 2) {
                name = parts[0].trim();
                content = parts.slice(1).join(',').trim();
            } else {
                name = `学生${i + 1}`;
                content = line.trim();
            }

            container.appendChild(createStudentCard(i + 1, name, content));
        });

        closeModal();
        showToast(`已导入 ${lines.length} 份答案`, 'success');
    });
}

// ==================== 文件上传 ====================

function initFileUpload() {
    // 主界面上传按钮
    const fileInput = document.getElementById('file-upload-students');
    if (fileInput) {
        fileInput.addEventListener('change', async () => {
            if (!fileInput.files.length) return;
            await uploadStudentFile(fileInput.files[0], 'student-cards');
            fileInput.value = '';
        });
    }

    // 弹窗内上传
    const modalFileInput = document.getElementById('modal-file-upload');
    if (modalFileInput) {
        modalFileInput.addEventListener('change', async () => {
            if (!modalFileInput.files.length) return;
            const file = modalFileInput.files[0];
            try {
                const text = await readFileAsText(file);
                document.getElementById('batch-text').value = text;
                showToast('文件已加载', 'success');
            } catch (e) {
                showToast('文件读取失败', 'error');
            }
            modalFileInput.value = '';
        });
    }
}

async function uploadStudentFile(file, targetContainerId) {
    const formData = new FormData();
    formData.append('file', file);

    showLoading(true);
    try {
        const resp = await fetch('/api/upload/students', { method: 'POST', body: formData });
        const data = await resp.json();
        showLoading(false);

        if (!data.success) {
            showToast(data.error || '上传失败', 'error');
            return;
        }

        const container = document.getElementById(targetContainerId);
        container.innerHTML = '';

        data.students.forEach((s, i) => {
            container.appendChild(createStudentCard(i + 1, s.name, s.text));
        });

        showToast(`已导入 ${data.count} 份答案`, 'success');
    } catch (err) {
        showLoading(false);
        showToast('上传失败', 'error');
    }
}

function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = e => resolve(e.target.result);
        reader.onerror = reject;
        reader.readAsText(file, 'utf-8');
    });
}

// ==================== 历史记录 ====================

const MODE_LABELS = {
    text: '文本', pairwise: '互查', paper: '整卷', aigc: 'AIGC线索',
    // 已下线的模式仍要能显示：磁盘上的旧历史记录里带着这两个标记
    image: '图片(已下线)', api: 'API搜题(已下线)',
};

async function loadHistory() {
    const container = document.getElementById('history-content');
    try {
        const resp = await fetch('/api/history');
        const data = await resp.json();
        if (!data.success || !data.history || data.history.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">📋</span>
                    <h3>暂无检测历史</h3>
                    <p>完成检测后，结果将在此展示</p>
                </div>`;
            return;
        }

        container.innerHTML = '';

        data.history.forEach(entry => {
            const stats = entry.statistics || {};
            const ts = (entry.timestamp || '').replace('T', ' ').slice(0, 19);
            const mode = MODE_LABELS[entry.mode] || entry.mode;
            const plagiarismRate = percent(stats.plagiarism_rate);
            const avgScore = percent(stats.avg_score);

            const card = document.createElement('div');
            card.className = 'history-card';

            card.innerHTML = `
                <div class="history-card-header">
                    <span class="history-mode-badge">${mode}</span>
                    <span class="history-time">${ts}</span>
                </div>
                <div class="history-stats">
                    <div class="history-stat">
                        <span class="history-stat-value">${stats.count || 0}</span>
                        <span class="history-stat-label">份数</span>
                    </div>
                    <div class="history-stat">
                        <span class="history-stat-value">${avgScore}</span>
                        <span class="history-stat-label">平均相似度</span>
                    </div>
                    <div class="history-stat">
                        <span class="history-stat-value history-stat-danger">${plagiarismRate}</span>
                        <span class="history-stat-label">抄袭率</span>
                    </div>
                    <div class="history-stat">
                        <span class="history-stat-value history-stat-danger">${stats.high_count || 0}</span>
                        <span class="history-stat-label">高风险</span>
                    </div>
                </div>
                <button class="btn btn-outline btn-sm history-view-btn" data-id="${entry.id}">查看详情</button>
            `;

            container.appendChild(card);
        });

        // 查看详情按钮
        container.querySelectorAll('.history-view-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                const resp = await fetch(`/api/history/${id}`);
                const data = await resp.json();
                if (data.success && data.detail) {
                    openHistoryDetail(data.detail);
                } else {
                    showToast('该记录无法读取', 'error');
                }
            });
        });
    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <span class="empty-icon">❌</span>
                <h3>加载失败</h3>
                <p>请刷新页面重试</p>
            </div>`;
    }
}

/** 历史详情按模式分派渲染；互查/整卷的矩阵与团伙数据存在 extra 里 */
function openHistoryDetail(detail) {
    const mode = detail.mode || 'text';
    const payload = Object.assign({}, detail.extra || {}, {
        mode,
        results: detail.results || [],
        statistics: detail.statistics || {},
        standard_answer: detail.standard_answer || '',
        timestamp: detail.timestamp || '',
    });

    switchToDetectTab();
    renderResults(payload);
    showToast('已加载历史记录', 'success');
}
