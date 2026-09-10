# Editable安装漂移取证报告 (Packet: FR05)

## 审计目标
任务名：Editable安装漂移取证
只读核对 venv 中 zloop editable 指向、console script shebang、Python/SDK 版本与工作树源文件，判断入口是否加载旧版/错误路径。

## 直接证据

### Pip安装检查
```
Package                   Version           Location            Installer                                                                Editable project location
------------------------- ----------------- ------------------- ------------------------------------------------------------------------ -------------------------
aiohappyeyeballs          2.7.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
aiohttp                   3.14.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
aiohttp-fast-zlib         0.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
aiosignal                 1.4.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
annotated-doc             0.0.5                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
annotated-types           0.8.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
anyio                     4.14.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
anywidget                 0.11.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
apsw                      3.53.4.0                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
arch                      8.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
astropy                   8.0.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
astropy-iers-data         0.2026.8.3.0.53.6                     C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
asttokens                 3.0.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
attrs                     26.1.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
av                        18.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
bitarray                  3.10.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ccxt                      4.5.70                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
certifi                   2026.6.17                             C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cffi                      2.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
charset-normalizer        3.4.7                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ckzg                      2.1.8                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
clarabel                  0.11.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
click                     8.4.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cloudpickle               3.1.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cody-special              1.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
colorama                  0.4.6                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
comm                      0.2.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
comtypes                  1.4.16                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
contourpy                 1.3.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cryptography              49.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ctranslate2               4.8.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cvxpy                     1.9.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cycler                    0.12.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
cytoolz                   1.1.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
dateparser                1.4.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
defusedxml                0.7.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
deno                      2.9.4                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
dill                      0.4.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
duckdb                    1.5.5                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
easyocr                   1.7.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth_abi                   5.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-account               0.13.7                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-hash                  0.8.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-keyfile               0.8.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-keys                  0.7.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-rlp                   2.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-typing                6.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
eth-utils                 6.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
executing                 2.2.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
Farama-Notifications      0.0.6                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
faster-whisper            1.2.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
filelock                  3.29.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
fire                      0.7.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
flatbuffers               25.12.19                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
fonttools                 4.63.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
frozenlist                1.8.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
fsspec                    2026.4.0                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
greenlet                  3.5.5                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
gymnasium                 1.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
h11                       0.16.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
hexbytes                  1.3.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
hf-xet                    1.5.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
highspy                   1.15.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
httpcore                  1.0.9                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
httpx                     0.28.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
huggingface_hub           1.24.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
idna                      3.18                                  C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ImageIO                   2.37.4                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
iniconfig                 2.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ipython                   9.16.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ipython_pygments_lexers   1.1.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ipywidgets                8.1.8                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
jedi                      0.20.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
Jinja2                    3.1.6                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
joblib                    1.5.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
jsonschema                4.26.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
jsonschema-specifications 2025.9.1                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
jupyterlab_widgets        3.0.16                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
kiwisolver                1.5.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
lazy-loader               0.5                                   C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
lets_be_rational          1.1.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
lightgbm                  4.7.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
llvmlite                  0.48.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
loguru                    0.7.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
markdown-it-py            4.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
MarkupSafe                3.0.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
matplotlib                3.11.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
matplotlib-inline         0.2.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
mdurl                     0.1.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
MouseInfo                 0.1.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
mplfinance                0.12.10b0                             C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
mpmath                    1.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
multidict                 6.7.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
mypy_extensions           1.1.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
narwhals                  2.24.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
networkx                  3.6.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
ninja                     1.13.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
numba                     0.66.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
numpy                     2.4.6                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
onnxruntime               1.27.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
opencv-python             5.0.0.93                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
opencv-python-headless    5.0.0.93                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
orjson                    3.11.9                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
osqp                      1.1.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
packaging                 26.2                                  C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pandas                    3.0.5                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
parsimonious              0.10.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
parso                     0.8.7                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
patsy                     1.0.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pdfminer.six              20260107                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pdfplumber                0.11.10                               C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
piecewise-rational        1.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pillow                    12.3.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pip                       25.3                                  C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
playwright                1.62.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
plotly                    6.9.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pluggy                    1.6.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
prompt_toolkit            3.0.53                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
propcache                 0.5.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
protobuf                  7.35.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
psutil                    7.2.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
psygnal                   0.15.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pure_eval                 0.2.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
py-vollib                 1.0.12                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyarrow                   25.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyAutoGUI                 0.9.54                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pybind11                  3.0.4                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyclipper                 1.4.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pycparser                 3.0                                   C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pycryptodome              3.23.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pydantic                  2.13.4                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pydantic_core             2.46.4                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyee                      13.0.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyerfa                    2.0.1.5                               C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyGetWindow               0.0.9                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
Pygments                  2.20.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyMsgBox                  2.0.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyparsing                 3.3.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pypdf                     6.14.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pypdfium2                 5.12.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyperclip                 1.11.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyportfolioopt            1.6.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyRect                    0.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyScreeze                 1.0.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pystray                   0.19.5                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pytesseract               0.3.13                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pytest                    9.1.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
python-bidi               0.6.11                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
python-dateutil           2.9.0.post0                           C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
python-dotenv             1.2.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pytweening                1.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pytz                      2026.3.post1                          C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pyunormalize              17.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pywin32                   312                                   C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
pywinauto                 0.6.9                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
PyYAML                    6.0.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
qdldl                     0.1.9.post1                           C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
qrcode                    8.2                                   C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
rapidocr-onnxruntime      1.2.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
referencing               0.37.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
regex                     2026.7.19                             C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
requests                  2.34.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
rich                      15.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
riskfolio-lib             7.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
rlp                       4.1.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
rpds-py                   2026.6.3                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
safetensors               0.8.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
schedule                  1.2.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
scikit-base               0.13.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
scikit-image              0.26.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
scikit-learn              1.9.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
scipy                     1.18.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
scs                       3.2.11                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
setuptools                83.0.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
shapely                   2.1.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
shellingham               1.5.4                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
simplejson                3.20.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
six                       1.17.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
sparsediffpy              0.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
stable_baselines3         2.9.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
stack-data                0.6.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
statsmodels               0.14.6                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
sympy                     1.14.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
termcolor                 3.3.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
threadpoolctl             3.6.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
tifffile                  2026.7.31                             C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
tokenizers                0.22.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
toolz                     1.1.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
torch                     2.13.0+cpu                            C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
torchvision               0.28.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
tqdm                      4.69.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
traitlets                 5.16.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
transformers              5.14.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
typer                     0.27.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
types-requests            2.33.0.20260712                       C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
typing_extensions         4.16.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
typing-inspection         0.4.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
tzdata                    2026.3                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
tzlocal                   5.4.4                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
uiautomation              2.0.29                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
urllib3                   2.7.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
vectorbt                  1.1.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
vollib                    1.0.11                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
wcwidth                   0.8.2                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
web3                      7.16.0                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
websocket-client          1.9.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
websockets                15.0.1                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
widgetsnbextension        4.0.15                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
win32_setctime            1.2.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
winloop                   0.6.3                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
xlsxwriter                3.2.9                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
yarl                      1.24.2                                C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
youtube-transcript-api    1.2.4                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
yt-dlp                    2026.7.4                              C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
zlib-ng                   1.0.0                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
zloop                     0.1.0             E:\zcode\zloop-gen8 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip
zxing-cpp                 3.1.1                                 C:\Users\hzq00\AppData\Local\Programs\Python\Python314\Lib\site-packages pip

```

### Git工作区状态
```
fatal: not a git repository (or any of the parent directories): .git

```

## 机械命令与退出码
- pip list -v: Exit code 0
- git status: Exit code 1

