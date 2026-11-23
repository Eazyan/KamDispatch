CREATE TABLE bot_role_preferences (
    mode text NOT NULL,
    actor text NOT NULL,
    PRIMARY KEY (mode, actor)
);
