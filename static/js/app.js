/**
 * AntiCopy - 作业抄袭检测系统前端逻辑
 */

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initModeSelector();
    initStudentCards('student-count', 'student-cards', 'generate-cards', 'decrease-count', 'increase-count');
    initStudentCards('student-count-img', 'student-cards-img', 'generate-cards-img', 'decrease-count-img', 'increase-count-img');
    initStudentCards('student-count-api', 'student-cards-api', 'generate-cards-api', 'decrease-count-api', 'increase-count-api');
    initImageUpload();
    initDetection();
    initSettings();
    initActions();
    initBatchImport();
    initFileUpload();
    loadHistory();

    // 默认生成卡片
    generateCards('student-cards', 3);
    generateCards('student-cards-img', 3);
    generateCards('student-cards-api', 3);
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

// ==================== 学生卡片生成 ====================

function initStudentCards(countId, cardsId, btnId, decreaseId, increaseId) {
    const countInput = document.getElementById(countId);
    const decreaseBtn = document.getElementById(decreaseId);
    const increaseBtn = document.getElementById(increaseId);
    const generateBtn = document.getElementById(btnId);

    if (!countInput) return;

    decreaseBtn.addEventListener('click', () => {
        let val = parseInt(countInput.value) || 1;
        if (val > 1) countInput.value = val - 1;
    });

    increaseBtn.addEventListener('click', () => {
        let val = parseInt(countInput.value) || 1;
        if (val < 50) countInput.value = val + 1;
    });

    generateBtn.addEventListener('click', () => {
        const count = parseInt(countInput.value) || 1;
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
        card.querySelector('.card-index').textContent = String(i + 1);
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

// ==================== 图片上传 ====================

function initImageUpload() {
    const zone = document.getElementById('upload-zone');
    const input = document.getElementById('image-upload');
    const preview = document.getElementById('upload-preview');
    const previewImg = document.getElementById('preview-img');
    const removeBtn = document.getElementById('remove-image');
    const content = zone.querySelector('.upload-content');

    if (!zone) return;

    // 拖拽
    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('dragover');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('dragover');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0 && files[0].type.startsWith('image/')) {
            handleImageFile(files[0]);
        }
    });

    input.addEventListener('change', () => {
        if (input.files.length > 0) {
            handleImageFile(input.files[0]);
        }
    });

    removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        preview.style.display = 'none';
        content.style.display = 'block';
        input.value = '';
    });

    function handleImageFile(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            previewImg.src = e.target.result;
            preview.style.display = 'block';
            content.style.display = 'none';
        };
        reader.readAsDataURL(file);
    }
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

    // 图片检测
    document.getElementById('btn-detect-image').addEventListener('click', async () => {
        const input = document.getElementById('image-upload');
        const students = collectStudents('student-cards-img');

        if (!input.files.length) {
            showToast('请上传题目图片', 'error');
            return;
        }
        if (!validateStudents(students)) return;

        await detectImage(input.files[0], students);
    });

    // API 搜题检测
    document.getElementById('btn-detect-api').addEventListener('click', async () => {
        const question = document.getElementById('question-input').value.trim();
        const students = collectStudents('student-cards-api');

        if (!question) {
            showToast('请输入题目内容', 'error');
            return;
        }
        if (!validateStudents(students)) return;

        await detectWithAPI(question, students);
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
        const resp = await fetch('/api/detect/text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ standard_answer: standard, students })
        });
        const data = await resp.json();
        if (data.success) {
            renderResults(data);
            showToast('检测完成！', 'success');
        } else {
            showToast(data.error || '检测失败', 'error');
        }
    } catch (err) {
        showToast('网络错误，请重试', 'error');
    }
    showLoading(false);
}

async function detectImage(file, students) {
    showLoading(true);
    try {
        const formData = new FormData();
        formData.append('image', file);
        formData.append('students', JSON.stringify(students));

        const resp = await fetch('/api/detect/image', {
            method: 'POST',
            body: formData
        });
        const data = await resp.json();
        if (data.success) {
            renderResults(data);
            showToast('检测完成！', 'success');
        } else {
            showToast(data.error || '检测失败', 'error');
        }
    } catch (err) {
        showToast('网络错误，请重试', 'error');
    }
    showLoading(false);
}

async function detectWithAPI(question, students) {
    showLoading(true);
    try {
        const resp = await fetch('/api/detect/api-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, students })
        });
        const data = await resp.json();
        if (data.success) {
            renderResults(data);
            showToast('搜题检测完成！', 'success');
        } else {
            showToast(data.error || '检测失败', 'error');
        }
    } catch (err) {
        showToast('网络错误，请重试', 'error');
    }
    showLoading(false);
}

// ==================== 结果渲染 ====================

function renderResults(data) {
    const results = data.results || [];
    const stats = data.statistics || {};

    // 隐藏检测面板，显示结果
    document.getElementById('tab-detect').style.display = 'none';
    const resultPanel = document.getElementById('result-panel');
    resultPanel.style.display = 'block';

    // 统计数据
    document.getElementById('stat-count').textContent = stats.count || 0;
    document.getElementById('stat-avg').textContent = ((stats.avg_score || 0) * 100).toFixed(1) + '%';
    document.getElementById('stat-high').textContent = stats.high_count || 0;
    document.getElementById('stat-rate').textContent = ((stats.plagiarism_rate || 0) * 100).toFixed(1) + '%';

    // 标准答案
    if (data.standard_answer) {
        const saCard = document.getElementById('standard-answer-display');
        saCard.style.display = 'block';
        document.getElementById('standard-answer-text').textContent = data.standard_answer;
        if (data.api_source) {
            const src = document.getElementById('api-source');
            src.textContent = data.api_source;
            src.style.display = 'inline';
        }
    } else {
        document.getElementById('standard-answer-display').style.display = 'none';
    }

    // 结果列表
    const list = document.getElementById('result-list');
    list.innerHTML = '';

    results.forEach((r, i) => {
        const level = r.level;
        const score = (r.weighted_score * 100).toFixed(1);
        const item = document.createElement('div');
        item.className = 'result-item';

        // Rank
        const rank = document.createElement('div');
        rank.className = `result-rank rank-${level}`;
        rank.textContent = String(i + 1);

        // Info
        const info = document.createElement('div');
        info.className = 'result-info';
        const name = document.createElement('div');
        name.className = 'result-name';
        name.textContent = r.student_name;
        if (r.photos && r.photos.length > 0) {
            const photoTag = document.createElement('span');
            photoTag.className = 'result-photo-tag';
            photoTag.textContent = `📷 ${r.photos.length}张`;
            name.appendChild(photoTag);
        }
        const details = document.createElement('div');
        details.className = 'result-details';
        details.textContent = `精确 ${(r.exact_match * 100).toFixed(1)}% · 序列 ${(r.sequence_similarity * 100).toFixed(1)}% · 关键词 ${(r.keyword_overlap * 100).toFixed(1)}%`;
        info.appendChild(name);
        info.appendChild(details);

        // Score
        const scoreDiv = document.createElement('div');
        scoreDiv.className = 'result-score';
        const scoreVal = document.createElement('span');
        scoreVal.className = `score-value score-${level}`;
        scoreVal.textContent = `${score}%`;
        const barOuter = document.createElement('div');
        barOuter.className = 'score-bar';
        const barInner = document.createElement('div');
        barInner.className = `score-bar-fill bar-${level}`;
        barInner.style.width = `${score}%`;
        barOuter.appendChild(barInner);
        scoreDiv.appendChild(scoreVal);
        scoreDiv.appendChild(barOuter);

        // Badge
        const badge = document.createElement('span');
        badge.className = `result-badge badge-${level}`;
        badge.textContent = getLevelText(level);

        item.appendChild(rank);
        item.appendChild(info);
        item.appendChild(scoreDiv);
        item.appendChild(badge);

        // 展开区：学生完整答案 + 答案照片（点击行展开）
        const expand = document.createElement('div');
        expand.className = 'result-expand';
        if (r.student_answer) {
            const label1 = document.createElement('div');
            label1.className = 'expand-label';
            label1.textContent = '学生答案';
            const ans = document.createElement('div');
            ans.className = 'expand-text';
            ans.textContent = r.student_answer;
            expand.appendChild(label1);
            expand.appendChild(ans);
        }
        if (r.photos && r.photos.length > 0) {
            const label2 = document.createElement('div');
            label2.className = 'expand-label';
            label2.textContent = `答案照片（${r.photos.length} 张，点击放大）`;
            const grid = document.createElement('div');
            grid.className = 'expand-photos';
            r.photos.forEach(url => {
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
            expand.appendChild(label2);
            expand.appendChild(grid);
        }
        if (expand.children.length > 0) {
            item.appendChild(expand);
            item.classList.add('expandable');
            item.addEventListener('click', (e) => {
                if (e.target.closest('a')) return;
                item.classList.toggle('expanded');
            });
        }

        list.appendChild(item);
    });

    // 保存结果用于导出
    window._lastResults = results;
    window._lastStatistics = stats;
    window._lastStandardAnswer = data.standard_answer || '';
    window._lastTimestamp = data.timestamp || '';
}

function getLevelText(level) {
    const map = { high: '高风险', medium: '中风险', low: '低风险', safe: '安全' };
    return map[level] || level;
}

// ==================== 操作按钮 ====================

function initActions() {
    document.getElementById('btn-export').addEventListener('click', async () => {
        if (!window._lastResults || window._lastResults.length === 0) {
            showToast('没有可导出的数据', 'warning');
            return;
        }

        try {
            const resp = await fetch('/api/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    results: window._lastResults,
                    statistics: window._lastStatistics || {},
                    standard_answer: window._lastStandardAnswer || '',
                    timestamp: window._lastTimestamp || ''
                })
            });
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = '抄袭检测报告.txt';
            a.click();
            URL.revokeObjectURL(url);
            showToast('报告已导出', 'success');
        } catch (err) {
            showToast('导出失败', 'error');
        }
    });

    document.getElementById('btn-reset').addEventListener('click', () => {
        document.getElementById('result-panel').style.display = 'none';
        document.getElementById('tab-detect').style.display = 'block';
        document.getElementById('standard-answer-display').style.display = 'none';
        window._lastResults = null;
        window._lastStatistics = null;
    });
}

// ==================== 设置面板 ====================

function initSettings() {
    const sliders = [
        { id: 'weight-exact', display: 'weight-exact-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-sequence', display: 'weight-sequence-val', format: v => (v / 100).toFixed(2) },
        { id: 'weight-keyword', display: 'weight-keyword-val', format: v => (v / 100).toFixed(2) },
        { id: 'threshold-high', display: 'threshold-high-val', format: v => v + '%' },
        { id: 'threshold-medium', display: 'threshold-medium-val', format: v => v + '%' }
    ];

    sliders.forEach(({ id, display, format }) => {
        const slider = document.getElementById(id);
        const val = document.getElementById(display);
        if (slider && val) {
            slider.addEventListener('input', () => {
                val.textContent = format(parseInt(slider.value));
            });
        }
    });

    // 加载服务端当前设置
    fetch('/api/settings')
        .then(r => r.json())
        .then(data => {
            if (!data.success) return;
            const w = data.weights;
            const t = data.thresholds;
            if (w) {
                setSlider('weight-exact', Math.round((w.exact_match || 0.4) * 100));
                setSlider('weight-sequence', Math.round((w.sequence_similarity || 0.35) * 100));
                setSlider('weight-keyword', Math.round((w.keyword_overlap || 0.25) * 100));
            }
            if (t) {
                setSlider('threshold-high', Math.round((t.high || 0.8) * 100));
                setSlider('threshold-medium', Math.round((t.medium || 0.5) * 100));
            }
        })
        .catch(() => {});

    // 保存设置
    document.getElementById('btn-save-settings').addEventListener('click', async () => {
        const weights = {
            exact_match: parseInt(document.getElementById('weight-exact').value) / 100,
            sequence_similarity: parseInt(document.getElementById('weight-sequence').value) / 100,
            keyword_overlap: parseInt(document.getElementById('weight-keyword').value) / 100
        };
        const thresholds = {
            high: parseInt(document.getElementById('threshold-high').value) / 100,
            medium: parseInt(document.getElementById('threshold-medium').value) / 100
        };

        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ weights, thresholds })
            });
            const data = await resp.json();
            if (data.success) {
                showToast('设置已保存', 'success');
            } else {
                showToast(data.error || '保存失败', 'error');
            }
        } catch (err) {
            showToast('保存失败', 'error');
        }
    });
}

function setSlider(id, value) {
    const slider = document.getElementById(id);
    const display = document.getElementById(id + '-val');
    if (!slider || !display) return;
    slider.value = value;
    if (id.startsWith('weight')) {
        display.textContent = (value / 100).toFixed(2);
    } else {
        display.textContent = value + '%';
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

    const icons = { success: '✅', error: '❌', warning: '⚠️' };
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
    }, 3000);
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
        const modeLabels = { text: '文本', image: '图片', api: 'API搜题' };

        data.history.forEach(entry => {
            const stats = entry.statistics || {};
            const ts = (entry.timestamp || '').replace('T', ' ').slice(0, 19);
            const mode = modeLabels[entry.mode] || entry.mode;
            const plagiarismRate = ((stats.plagiarism_rate || 0) * 100).toFixed(1);
            const avgScore = ((stats.avg_score || 0) * 100).toFixed(1);

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
                        <span class="history-stat-value">${avgScore}%</span>
                        <span class="history-stat-label">平均相似度</span>
                    </div>
                    <div class="history-stat">
                        <span class="history-stat-value history-stat-danger">${plagiarismRate}%</span>
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
                    const d = data.detail;
                    window._lastResults = d.results;
                    window._lastStatistics = d.statistics;
                    window._lastStandardAnswer = d.standard_answer || '';
                    window._lastTimestamp = d.timestamp || '';
                    renderResults({
                        results: d.results,
                        statistics: d.statistics,
                        standard_answer: d.standard_answer,
                        timestamp: d.timestamp
                    });
                    // 切换到检测视图
                    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
                    document.querySelector('[data-tab="detect"]').classList.add('active');
                    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                    document.getElementById('tab-detect').classList.add('active');
                    showToast('已加载历史记录', 'success');
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
