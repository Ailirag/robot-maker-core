"""Verify historical prose against an immutable manifest and an ancestor Git blob.

Only explicit Markdown copies qualify. Current facts, geometry and physical
gates remain live inputs of their own consumers; this function grants nothing.
"""
import hashlib
from pathlib import Path, PurePosixPath
import subprocess

def _path(root,value):
    if (not isinstance(value,str) or "\\" in value or ":" in value
            or not value.endswith(".md") or PurePosixPath(value).is_absolute()
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise ValueError("HISTORICAL_CONTEXT: explicit relative Markdown path required")
    path=(root/value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("HISTORICAL_CONTEXT: archive outside repository")
    return path

def verify(root,manifest_sources,bindings):
    root=Path(root).resolve()
    if not isinstance(bindings,dict) or not isinstance(manifest_sources,dict):
        raise ValueError("HISTORICAL_CONTEXT: invalid bindings")
    rows=[]
    for original,binding in bindings.items():
        try:
            _path(root,original)
            if not isinstance(binding,dict) or set(binding)!={"path","commit","reason"}:
                raise ValueError("explicit archive, commit and reason required")
            path=_path(root,binding["path"])
            if (binding["path"]==original or not isinstance(binding["reason"],str)
                    or len(binding["reason"].strip())<40):
                raise ValueError("invalid archive path or undocumented reason")
            head=binding["commit"]
            if not isinstance(head,str) or len(head)!=40 or any(c not in "0123456789abcdef" for c in head):
                raise ValueError("invalid original commit")
            subprocess.run(["git","merge-base","--is-ancestor",head,"HEAD"],cwd=root,
                check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            blob=subprocess.check_output(["git","show",head+":"+original],cwd=root,stderr=subprocess.PIPE)
            expected=manifest_sources[original]
            size,digest=expected["bytes"],expected["sha256"]
            if type(size) is not int or size<0 or not isinstance(digest,str) or len(digest)!=64:
                raise ValueError("invalid immutable input pin")
            raw=path.read_bytes()
            if blob.replace(b"\r\n",b"\n").replace(b"\r",b"\n")!=raw or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError("archive/Git bytes do not match immutable input")
            rows.append(dict(original=original,path=binding["path"],commit=head,reason=binding["reason"],
                bytes=size,sha256=digest))
        except (KeyError,TypeError,OSError,ValueError,subprocess.CalledProcessError) as exc:
            raise ValueError("HISTORICAL_CONTEXT: "+str(original)+": "+str(exc)) from exc
    return rows
