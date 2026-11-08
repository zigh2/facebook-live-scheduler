# fb-live-scheduler

بث فيسبوك مباشر **آلي** لملفات فيديو (mp4 وغيرها) بأوقات محددة، بدون أي تدخل يدوي، عبر **OBS Studio** وواجهة **Facebook Live Video API**.

**الإصدار الحالي: `0.1.0`**

## ماذا يفعل؟
في كل موعد يقوم البرنامج بما كنت تفعله يدوياً:
1. يجهّز OBS (مشهد + مصدر وسائط يُحجَّم الفيديو داخله تلقائياً مهما كانت دقته، من 720 إلى 4K).
2. ينشئ بثاً جديداً على صفحتك بالعنوان والوصف عبر الـ API ويحصل على مفتاح البث.
3. يضع المفتاح في OBS ويبدأ البث ثم يشغّل الفيديو.
4. ينتظر انتهاء الملف (أياً كان طوله)، ثم يوقف البث وينهيه على فيسبوك.

- بثان ثابتان يومياً (ملف وعنوان ووصف ثابت) من `config.yaml`.
- بث متغير (وقت وعنوان ووصف وملف مختلف كل يوم) من ملف `queue.csv` يمكن تعديله في Excel أثناء عمل البرنامج.
- فحص الملف قبل البث (وجود صوت، ترميز، مدة) بـ ffprobe، فيسبوك يشترط وجود صوت.
- إعادة محاولة تلقائية، مهلة تأخير عند إعادة تشغيل الجهاز، وعدم تكرار بث نفسه بعد إعادة التشغيل.
- لا يلمس بثاً يدوياً جارياً في OBS.
- تنبيهات تيليجرام عند الفشل (اختياري، مجاني).

## المتطلبات
- Windows (أو Linux لاحقاً)، **Python 3.10+**
- **OBS Studio 28+** (خادم WebSocket مدمج)
- **FFmpeg** ضمن PATH (للحصول على `ffprobe`، موصى به)
- صفحة فيسبوك أنت مشرف عليها ومؤهلة للبث المباشر، وتطبيق في Meta for Developers (مجاني)

## التثبيت (Windows)
```
scripts\install.bat
```
ينشئ بيئة بايثون ويثبّت الحزمة وينسخ ملفات الإعداد (`config.yaml` و`.env` و`queue.csv`).

## الإعداد
### 1) OBS (مرة واحدة)
- `Tools > WebSocket Server Settings`: فعّل الخادم، المنفذ 4455، فعّل المصادقة وضع كلمة مرور، وضعها في `.env` كـ `OBS_PASSWORD`.
- `Settings > Video`: Base و Output = 1920x1080 (أو 1280x720)، FPS = 30.
- `Settings > Output > Streaming`: مُرمِّز H.264 (يفضل NVENC/QSV/AMF إن توفر)، Rate Control = CBR، Bitrate بنحو 4500 كيلوبت، Keyframe Interval = 2 ثانية. تحقق من أحدث توصيات فيسبوك للبث.
- شغّل `fblive obs-setup` لينشئ البرنامج المشهدين والمصدر.

### 2) فيسبوك (مرة واحدة)
1. أنشئ تطبيقاً في https://developers.facebook.com (نوع Business) وأضف نفسك مشرفاً.
2. من Graph API Explorer احصل على User token بالصلاحيات: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `publish_video`.
3. نفّذ `fblive setup-token` وأدخل App ID وApp Secret والرمز؛ يحفظ رمز الصفحة (طويل الأمد) و`FB_PAGE_ID` في `.env`.

> بعض صلاحيات التطبيقات تتطلب App Review عند استخدامها لحسابات الآخرين. لصفحتك أنت وأنت مشرف على التطبيق يعمل غالباً في وضع التطوير، وتحقق من ذلك بأول تجربة.
> فيسبوك يشترط حداً أدنى من المتابعين وعمر الحساب للبث (راجع توثيق Live API).

### 3) الجدول
- عدّل `config.yaml`: الأوقات والملفات والعناوين للبثين الثابتين.
- البث المتغير: أضف سطراً لكل بث في `queue.csv`:
  ```
  date,time,title,description,file
  2026-11-09,19:30,عنوان اليوم,وصف اليوم,D:/videos/new.mp4
  ```
  (احفظ الملف بصيغة UTF-8؛ الفاصلة أو الفاصلة المنقوطة كلاهما مقبول.)

## الاستخدام
```
fblive check            # يتحقق من الرمز وOBS والملفات
fblive plan --days 3    # يعرض البثوث القادمة
fblive now --stream morning --test   # بث تجريبي غير منشور (UNPUBLISHED)
fblive run              # يشغّل الجدولة (اتركه يعمل)
```
جرّب `--test` أولاً قبل أي بث عام.

## التشغيل التلقائي مع Windows
```
powershell -ExecutionPolicy Bypass -File scripts\register-task-windows.ps1
```
يسجّل مهمة تبدأ عند تسجيل دخولك وتُعاد تلقائياً إن توقفت. يجب أن يبقى الجهاز شغالاً ومسجلاً الدخول (OBS يحتاج سطح المكتب). على Linux استعمل `scripts/fblive.service`.

يمكن ضبط `obs.executable` في `config.yaml` ليشغّل البرنامج OBS تلقائياً إن كان مغلقاً.

## تحديث المشروع على GitHub بدون أوامر
ارفع ملف المشروع المضغوط (`.zip` أو `.7z`) إلى مجلد **`incoming/`** (Add file > Upload files) فيعمل `extract-incoming.yml` تلقائياً: يفك الأرشيف، يستبدل ملفات المشروع (ويحذف القديمة)، يحذف الأرشيف، ثم يعمل commit ووسماً بصيغة `v<الإصدار>-ddmmyy`، والإصدار يؤخذ من `src/fblive/__init__.py`.
- مجلد `.github/` ومجلد `incoming/` لا يُمسّان (صلاحيات GITHUB_TOKEN لا تسمح بتعديل workflows).
- أرشيف فارغ أو غير صالح يُرفض ولا يمسح المشروع.

## الأمان
- لا ترفع `.env` ولا `config.yaml` (مستثنيان في `.gitignore`).
- رمز الصفحة يتيح البث باسم صفحتك: احفظه كسر. إن تسرّب فأعد توليده.

## ملاحظات وحدود
- فيسبوك يتطلب صوتاً في البث؛ الملف بلا صوت يُرفض قبل البدء.
- إذا انقطع البث في المنتصف يُسجَّل `interrupted` ويصلك تنبيه، ولا يُعاد من البداية تلقائياً.
- يعمل البرنامج بالبث المباشر فقط (يبدأ ويُنهى لحظة الموعد). الجدولة المسبقة عبر الـ API (حتى 7 أيام) غير مستعملة.
- محرك FFmpeg المباشر (بدون OBS) مخطط لإصدار لاحق (لـ Linux بدون شاشة).

## التطوير
```
pip install -e ".[dev]"
pytest
```
الاختبارات تشغّل خادمي OBS وGraph API وهميين محلياً.

---

# English summary
Unattended, scheduled Facebook Live broadcasts of video files using OBS (obs-websocket v5) and the Facebook Live Video API. Two fixed daily slots in `config.yaml`, plus a variable slot driven by `queue.csv`. Run `scripts\install.bat`, configure OBS WebSocket, run `fblive setup-token`, edit `config.yaml`, check with `fblive check`, test with `fblive now --stream morning --test`, then `fblive run`. To update the repo, upload a `.zip`/`.7z` of the project into `incoming/` and the workflow will unpack and replace it.
