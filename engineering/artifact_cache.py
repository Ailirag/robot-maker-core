"""Disposable content-keyed byte cache, never an acceptance record.

Callers must supply complete semantic inputs and producer/runtime identity.
Corrupt/missing entries recompute; arbitrary code is never deserialized.
"""
import base64,hashlib,json,os,tempfile
from pathlib import Path

def digest(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf8")

class Cache:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.stats={"hit":0,"miss":0}

    def key(self,kind,inputs):
        return digest(encode({"schema":1,"procedure":digest(Path(__file__).read_bytes()),"kind":kind,"inputs":inputs}))

    def _read(self,key):
        try:
            path=self.directory/(key+".json")
            if path.is_symlink():return None
            record=json.loads(path.read_bytes())
            if record["key"]!=key:return None
            data=base64.b64decode(record["data"],validate=True)
            if digest(data)!=record["sha256"]:return None
            return data
        except (OSError,ValueError,KeyError,TypeError):return None

    def probe(self,kind,inputs):
        key=self.key(kind,inputs)
        return {"key":key,"status":"BYPASS" if os.environ.get("ENGINEERING_CACHE_DISABLE")=="1" else ("HIT" if self._read(key) is not None else "MISS")}

    def get(self,kind,inputs,build):
        if os.environ.get("ENGINEERING_CACHE_DISABLE")=="1":
            self.stats["miss"]+=1
            data=build()
            if not isinstance(data,bytes):raise TypeError("CACHE: producer must return bytes")
            return data
        key=self.key(kind,inputs)
        data=self._read(key)
        if data is not None:
            self.stats["hit"]+=1
            return data
        self.stats["miss"]+=1
        data=build()
        if not isinstance(data,bytes):raise TypeError("CACHE: producer must return bytes")
        record=encode({"key":key,"sha256":digest(data),"data":base64.b64encode(data).decode("ascii")})
        self.directory.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory,prefix="entry-",delete=False) as stream:
                temporary=Path(stream.name);stream.write(record)
            os.replace(temporary,self.directory/(key+".json"))
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()
        return data

def default_cache():
    directory=os.environ.get("ENGINEERING_CACHE_DIR")
    return Cache(Path(directory) if directory else Path(tempfile.gettempdir())/"engineering-artifacts-v1")
