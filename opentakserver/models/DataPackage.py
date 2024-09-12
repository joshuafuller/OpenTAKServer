import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from flask import current_app as app
from flask_login import current_user
from werkzeug.utils import secure_filename

from opentakserver.extensions import db
from sqlalchemy import Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opentakserver.forms.data_package_form import DataPackageForm
from opentakserver.functions import iso8601_string_from_datetime


@dataclass
class DataPackage(db.Model):
    __tablename__ = 'data_packages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String, unique=True)
    hash: Mapped[str] = mapped_column(String, unique=True)
    creator_uid: Mapped[str] = mapped_column(String, ForeignKey("euds.uid"))
    submission_time: Mapped[datetime] = mapped_column(DateTime)
    submission_user: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=True)
    keywords: Mapped[str] = mapped_column(String, nullable=True)
    mime_type: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String, nullable=True)
    expiration: Mapped[str] = mapped_column(String, nullable=True)
    install_on_enrollment: Mapped[bool] = mapped_column(Boolean, default=False)
    install_on_connection: Mapped[bool] = mapped_column(Boolean, default=False)
    eud: Mapped["EUD"] = relationship(back_populates="data_packages")
    certificate = relationship("Certificate", back_populates="data_package", uselist=False)
    user = relationship("User", back_populates="data_packages")

    def from_wtform(self, form: DataPackageForm):
        self.filename = secure_filename(form.file.data.filename)
        self.hash = form.hash.data
        self.creator_uid = form.creatorUid.data or str(uuid.uuid4())
        self.submission_user = current_user.id if current_user.is_authenticated else None
        self.submission_time = datetime.now()
        self.mime_type = form.file.data.mimetype
        self.size = os.path.getsize(os.path.join(app.config.get("UPLOAD_FOLDER"), self.filename))

    def serialize(self):
        return {
            'filename': self.filename,
            'hash': self.hash,
            'creator_uid': self.creator_uid,
            'submission_time': self.submission_time,
            'submission_user': self.submission_user,
            'keywords': self.keywords,
            'mime_type': self.mime_type,
            'size': self.size,
            'tool': self.tool,
            'expiration': self.expiration,
            'install_on_enrollment': self.install_on_enrollment,
            'install_on_connection': self.install_on_connection
        }

    def to_json(self, include_eud=True):
        return {
            'filename': self.filename,
            'hash': self.hash,
            'creator_uid': self.creator_uid,
            'submission_time': iso8601_string_from_datetime(self.submission_time),
            'submission_user': self.user.username if self.user else None,
            'keywords': self.keywords,
            'mime_type': self.mime_type,
            'size': self.size,
            'tool': self.tool,
            'expiration': self.expiration,
            'eud': self.eud.to_json(False) if include_eud and self.eud else None,
            'install_on_enrollment': self.install_on_enrollment,
            'install_on_connection': self.install_on_connection
        }
